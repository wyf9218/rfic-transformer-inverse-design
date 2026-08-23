#!/usr/bin/env python3
"""Build an audited real-EMX input table for the pairwise surrogate smoke.

The input sample and geometry manifests are expected to be a response-blind
subset copied from a formal stable index. Every Touchstone file is rehashed and
its embedded EMX/process/port/frequency provenance is checked before a row can
enter the output table.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rfic_transformer_inverse_design.analysis.extraction import (  # noqa: E402
    multiport_single_ended_to_differential_z,
)
from rfic_transformer_inverse_design.sim.touchstone import load_touchstone  # noqa: E402


DEFAULT_GEOMETRY_COLUMNS = (
    "primary_outer_width_um",
    "primary_outer_height_um",
    "primary_terminal_y_span_um",
    "primary_feed_extension_um",
    "secondary_outer_width_um",
    "secondary_outer_height_um",
    "secondary_terminal_y_span_um",
    "secondary_feed_extension_um",
    "shared_trace_width_um",
    "offset_um",
)
FEATURE_RANGES = {
    "lp_nh_center": (0.5, 3.0),
    "ls_nh_center": (0.5, 3.0),
    "q_center": (5.0, 25.0),
    "k_abs_center": (0.0, 0.8),
}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    sample_manifest_path = Path(args.sample_manifest).expanduser().resolve()
    geometry_manifest_path = Path(args.geometry_manifest).expanduser().resolve()
    s4p_dir = Path(args.s4p_dir).expanduser().resolve()
    output_csv = Path(args.output_csv).expanduser().resolve()
    output_manifest = Path(args.output_manifest).expanduser().resolve()
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.parent.mkdir(parents=True, exist_ok=True)

    checks: dict[str, bool] = {
        "sample_manifest_exists": sample_manifest_path.is_file(),
        "geometry_manifest_exists": geometry_manifest_path.is_file(),
        "s4p_directory_exists": s4p_dir.is_dir(),
    }
    rejects: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    sample_manifest = _read_json(sample_manifest_path)
    geometry_manifest = _read_json(geometry_manifest_path)
    sample_items = list(sample_manifest.get("samples") or [])
    geometry_items = list(geometry_manifest.get("samples") or [])
    geometry_columns = tuple(geometry_manifest.get("geometry_columns") or ())
    checks.update(
        {
            "response_blind_selection": sample_manifest.get("selection_is_response_blind") is True,
            "formal_stable_index_selection_recorded": "formal raw-80000 stable index"
            in str(sample_manifest.get("selection_rule") or "").lower(),
            "sample_count_matches_manifest": len(sample_items)
            == int(sample_manifest.get("sample_count") or -1),
            "geometry_count_matches_manifest": len(geometry_items)
            == int(geometry_manifest.get("sample_count") or -1),
            "sample_and_geometry_counts_match": len(sample_items) == len(geometry_items),
            "geometry_columns_are_expected_ten": geometry_columns == DEFAULT_GEOMETRY_COLUMNS,
            "shared_trace_width_contract_pass": geometry_manifest.get(
                "shared_trace_width_contract_pass"
            )
            is True,
        }
    )
    geometry_by_index = {
        int(item["index"]): item for item in geometry_items if "index" in item
    }
    sample_indices = [int(item["index"]) for item in sample_items if "index" in item]
    checks["sample_indices_unique"] = len(sample_indices) == len(set(sample_indices))
    checks["sample_geometry_index_sets_match"] = set(sample_indices) == set(geometry_by_index)

    for sample in sample_items:
        index = int(sample.get("index", -1))
        try:
            geometry_item = geometry_by_index[index]
            s4p_path = s4p_dir / f"{index:06d}.s4p"
            row = _build_row(
                sample,
                geometry_item,
                s4p_path,
                args,
                geometry_columns,
            )
            rows.append(row)
        except Exception as exc:  # noqa: BLE001
            rejects.append(
                {
                    "index": index,
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            )

    audited_rows = rows
    excluded_by_row_policy: list[dict[str, Any]] = []
    if args.row_policy == "declared_range_only":
        rows = []
        for row in audited_rows:
            if bool(row.get("audit__in_declared_physical_range")):
                rows.append(row)
            else:
                excluded_by_row_policy.append(
                    {
                        "stable_index": int(row["stable_index"]),
                        "reason": "OUTSIDE_DECLARED_LP_LS_Q_ABS_K_RANGE",
                    }
                )

    geometry_vectors = [
        tuple(float(row[f"geom__{column}"]) for column in geometry_columns)
        for row in audited_rows
    ]
    touchstone_hashes = [str(row["touchstone_sha256"]) for row in audited_rows]
    checks.update(
        {
            "all_manifest_rows_audited": len(audited_rows) == len(sample_items),
            "no_rejected_rows": not rejects,
            "touchstone_hashes_unique": len(touchstone_hashes) == len(set(touchstone_hashes)),
            "geometry_vectors_unique": len(geometry_vectors) == len(set(geometry_vectors)),
            "row_count_meets_minimum": len(rows) >= int(args.min_rows),
        }
    )
    overall_status = "PASS" if checks and all(checks.values()) else "FAIL"
    if overall_status == "PASS":
        _write_csv(output_csv, rows)
    else:
        output_csv.unlink(missing_ok=True)

    feature_summary = _feature_summary(rows)
    source_feature_summary = _feature_summary(audited_rows)
    manifest = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "decision": (
            "REAL_EMX_PAIRWISE_SMOKE_INPUT_READY"
            if overall_status == "PASS"
            else "DO_NOT_RUN_PAIRWISE_SMOKE"
        ),
        "training_count": len(rows),
        "source_audited_count": len(audited_rows),
        "row_policy": args.row_policy,
        "training_view_is_response_blind": args.row_policy == "all_audited",
        "excluded_by_row_policy_count": len(excluded_by_row_policy),
        "excluded_by_row_policy": excluded_by_row_policy,
        "training_csv": str(output_csv),
        "sample_manifest": str(sample_manifest_path),
        "geometry_manifest": str(geometry_manifest_path),
        "s4p_directory": str(s4p_dir),
        "selection_rule": sample_manifest.get("selection_rule"),
        "selection_is_response_blind": sample_manifest.get("selection_is_response_blind"),
        "source_role": "FORMAL_RAW_80000_REAL_EMX_RESPONSE_BLIND_SMOKE_SUBSET",
        "emx_contract": {
            "host": args.required_host,
            "process_token": args.required_process_token,
            "port_pairs_zero_based": [[0, 1], [2, 3]],
            "q_definition": "min(Qp, Qs)",
            "k_definition": "abs(M/sqrt(abs(Lp*Ls)))",
            "frequency_start_ghz": float(args.expected_frequency_start_ghz),
            "frequency_stop_ghz": float(args.expected_frequency_stop_ghz),
            "frequency_step_ghz": float(args.expected_frequency_step_ghz),
            "frequency_points": int(args.expected_frequency_points),
            "target_frequency_ghz": float(args.target_frequency_ghz),
        },
        "geometry_columns": [f"geom__{column}" for column in geometry_columns],
        "split_reference_columns": [
            "input__lp_nh_center",
            "input__ls_nh_center",
            "input__q_center",
            "input__k_abs_center",
        ],
        "feature_summary": feature_summary,
        "source_feature_summary": source_feature_summary,
        "checks": checks,
        "rejects": rejects,
        "csv_sha256": _sha256(output_csv) if output_csv.is_file() else "",
        "eligible_for_model_success_claim": False,
        "scientific_boundary": (
            "The source sample is response-blind; a declared-range-only training view applies the "
            "same accepted-pool physical-range contract used by the campaign. This is only an "
            "interface and optimization smoke. "
            "Its size is insufficient for a publication model conclusion, a uniformity claim, "
            "or a production checkpoint replacement."
        ),
    }
    output_manifest.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"overall_status={overall_status}")
    print(f"training_count={len(rows)}")
    print(f"manifest={output_manifest}")
    return 0 if overall_status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-manifest", required=True)
    parser.add_argument("--geometry-manifest", required=True)
    parser.add_argument("--s4p-dir", required=True)
    parser.add_argument("--output-csv", required=True)
    parser.add_argument("--output-manifest", required=True)
    parser.add_argument("--min-rows", type=int, default=60)
    parser.add_argument(
        "--row-policy",
        choices=("all_audited", "declared_range_only"),
        default="all_audited",
    )
    parser.add_argument("--required-host", default="mars.example.edu")
    parser.add_argument("--required-process-token", default="/TSMC65_05_12_26/")
    parser.add_argument("--expected-frequency-start-ghz", type=float, default=5.0)
    parser.add_argument("--expected-frequency-stop-ghz", type=float, default=60.0)
    parser.add_argument("--expected-frequency-step-ghz", type=float, default=0.5)
    parser.add_argument("--expected-frequency-points", type=int, default=111)
    parser.add_argument("--target-frequency-ghz", type=float, default=15.0)
    parser.add_argument("--frequency-tolerance-hz", type=float, default=1.0e5)
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if args.min_rows < 10:
        parser.error("--min-rows must be at least 10")
    if args.expected_frequency_points < 2 or args.expected_frequency_step_ghz <= 0.0:
        parser.error("invalid expected frequency grid")
    if not args.expected_frequency_start_ghz <= args.target_frequency_ghz <= args.expected_frequency_stop_ghz:
        parser.error("target frequency must lie inside the expected grid")
    return args


def _build_row(
    sample: dict[str, Any],
    geometry_item: dict[str, Any],
    s4p_path: Path,
    args: argparse.Namespace,
    geometry_columns: tuple[str, ...],
) -> dict[str, Any]:
    if not s4p_path.is_file():
        raise FileNotFoundError(s4p_path)
    expected_size = int(sample.get("size_bytes") or -1)
    if s4p_path.stat().st_size != expected_size:
        raise ValueError(f"size mismatch: {s4p_path.stat().st_size} != {expected_size}")
    content_sha256 = _sha256(s4p_path)
    if content_sha256 != str(sample.get("sha256") or ""):
        raise ValueError("Touchstone SHA256 differs from the response-blind copy manifest")
    text = s4p_path.read_text(encoding="utf-8")
    _validate_provenance(text, args)
    touchstone = load_touchstone(s4p_path)
    frequencies = np.asarray(touchstone.freqs_hz, dtype=float)
    s_matrix = np.asarray(touchstone.s_matrix, dtype=np.complex128)
    _validate_grid_and_ports(frequencies, s_matrix, args)
    metrics = _extract_target_metrics(touchstone, float(args.target_frequency_ghz) * 1.0e9)
    geometry = geometry_item.get("geometry") or {}
    values = [float(geometry[column]) for column in geometry_columns]
    if not np.all(np.isfinite(values)):
        raise ValueError("geometry vector contains non-finite values")
    row: dict[str, Any] = {
        "evaluation": str(sample.get("evaluation") or f"index_{int(sample['index']):06d}"),
        "stable_index": int(sample["index"]),
        "touchstone_path": str(s4p_path.resolve()),
        "touchstone_sha256": content_sha256,
        "input__lp_nh_center": metrics["lp_nh_center"],
        "input__ls_nh_center": metrics["ls_nh_center"],
        "input__q_center": metrics["q_center"],
        "input__k_abs_center": metrics["k_abs_center"],
        "audit__qp_center": metrics["qp_center"],
        "audit__qs_center": metrics["qs_center"],
        "audit__k_signed_center": metrics["k_signed_center"],
        "audit__target_frequency_hz": metrics["target_frequency_hz"],
        "audit__in_declared_physical_range": all(
            FEATURE_RANGES[name][0] <= float(metrics[name]) <= FEATURE_RANGES[name][1]
            for name in FEATURE_RANGES
        ),
    }
    row.update({f"geom__{column}": value for column, value in zip(geometry_columns, values)})
    return row


def _validate_provenance(text: str, args: argparse.Namespace) -> None:
    lowered = text.lower()
    if "synthetic contract fixture" in lowered:
        raise ValueError("synthetic fixture marker is forbidden")
    required = (
        "Touchstone simulation data from EMX version",
        f"EMX was run on {args.required_host}",
        str(args.required_process_token),
        "--cadence-pins=51",
        "--port=P001=P001:P001_G",
        "--port=P002=P002:P002_G",
        "--port=P003=P003:P003_G",
        "--port=P004=P004:P004_G",
    )
    missing = [token for token in required if token not in text]
    if missing:
        raise ValueError(f"missing embedded EMX provenance tokens: {missing}")
    declared_ports = re.findall(r"--port=(P\d{3})=", text)
    if sorted(declared_ports) != ["P001", "P002", "P003", "P004"]:
        raise ValueError(f"expected exactly P001-P004 declarations, got {declared_ports}")


def _validate_grid_and_ports(
    frequencies: np.ndarray,
    s_matrix: np.ndarray,
    args: argparse.Namespace,
) -> None:
    expected = np.linspace(
        float(args.expected_frequency_start_ghz) * 1.0e9,
        float(args.expected_frequency_stop_ghz) * 1.0e9,
        int(args.expected_frequency_points),
    )
    if s_matrix.shape != (len(expected), 4, 4):
        raise ValueError(f"expected S4P shape {(len(expected), 4, 4)}, got {s_matrix.shape}")
    if frequencies.shape != expected.shape:
        raise ValueError(f"expected {len(expected)} frequencies, got {len(frequencies)}")
    if float(np.max(np.abs(frequencies - expected))) > float(args.frequency_tolerance_hz):
        raise ValueError("Touchstone frequency grid differs from the declared 5-60 GHz contract")
    actual_steps = np.diff(frequencies)
    expected_step = float(args.expected_frequency_step_ghz) * 1.0e9
    if float(np.max(np.abs(actual_steps - expected_step))) > float(args.frequency_tolerance_hz):
        raise ValueError("Touchstone frequency step differs from the declared contract")
    if not np.all(np.isfinite(s_matrix.real)) or not np.all(np.isfinite(s_matrix.imag)):
        raise ValueError("Touchstone contains non-finite S-parameters")


def _extract_target_metrics(touchstone: Any, target_hz: float) -> dict[str, float]:
    frequencies = np.asarray(touchstone.freqs_hz, dtype=float)
    z_single = touchstone.to_z_parameters()
    z_diff = multiport_single_ended_to_differential_z(z_single, ((0, 1), (2, 3)))
    index = int(np.argmin(np.abs(frequencies - float(target_hz))))
    omega = 2.0 * math.pi * float(frequencies[index])
    z0 = z_diff[index]
    lp_h = float(np.imag(z0[0, 0]) / omega)
    ls_h = float(np.imag(z0[1, 1]) / omega)
    mutual_h = float(np.imag(z0[1, 0]) / omega)
    denominator = math.sqrt(max(abs(lp_h * ls_h), 1.0e-30))
    qp = _safe_div(float(np.imag(z0[0, 0])), float(np.real(z0[0, 0])))
    qs = _safe_div(float(np.imag(z0[1, 1])), float(np.real(z0[1, 1])))
    values = {
        "target_frequency_hz": float(frequencies[index]),
        "lp_nh_center": lp_h * 1.0e9,
        "ls_nh_center": ls_h * 1.0e9,
        "q_center": min(qp, qs),
        "k_abs_center": abs(mutual_h / denominator),
        "qp_center": qp,
        "qs_center": qs,
        "k_signed_center": mutual_h / denominator,
    }
    if not all(math.isfinite(float(value)) for value in values.values()):
        raise ValueError("extracted 15 GHz physical features contain non-finite values")
    return values


def _feature_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "row_count": len(rows),
        "declared_range_row_count": sum(
            bool(row.get("audit__in_declared_physical_range")) for row in rows
        ),
    }
    for feature in FEATURE_RANGES:
        values = np.asarray([float(row[f"input__{feature}"]) for row in rows], dtype=float)
        summary[feature] = {
            "minimum": float(np.min(values)) if values.size else None,
            "maximum": float(np.max(values)) if values.size else None,
            "median": float(np.median(values)) if values.size else None,
        }
    return summary


def _safe_div(numerator: float, denominator: float) -> float:
    if abs(denominator) <= 1.0e-12:
        return math.copysign(math.inf, numerator)
    return numerator / denominator


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("cannot write an empty training table")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
