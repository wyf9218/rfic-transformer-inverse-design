#!/usr/bin/env python3
"""Materialize a physical-feature targeted selection into an S4P geometry queue.

The selector keeps candidate geometry columns under `candidate__geom__*`.
For grounded-power-line S4P runs the shared trace width must be preserved as
`line_width_um`; otherwise the runner falls back to `primary_width_um`, which
can silently change the geometry that the surrogate selected.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


GEOMETRY_FIELDS = (
    "primary_outer_width_um",
    "primary_outer_height_um",
    "secondary_outer_width_um",
    "secondary_outer_height_um",
    "line_width_um",
    "primary_width_um",
    "secondary_width_um",
    "primary_terminal_y_span_um",
    "secondary_terminal_y_span_um",
    "offset_um",
    "primary_feed_extension_um",
    "secondary_feed_extension_um",
)

# primary_width_um and secondary_width_um are synchronized aliases of
# line_width_um.  They must not be counted as independent geometry dimensions.
CANONICAL_GEOMETRY_FIELDS = (
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
GEOMETRY_FINGERPRINT_SCHEMA = "mars56_grounded_s4p_geometry_v1"
DEFAULT_GEOMETRY_FINGERPRINT_QUANTIZATION_UM = 1.0e-6

PHYSICAL_FEATURE_FIELDS = (
    "lp_nh_center",
    "ls_nh_center",
    "q_center",
    "k_abs_center",
)

PROVENANCE_FIELDS = (
    "source_candidate_id",
    "geometry_fingerprint_sha256",
    "geometry_fingerprint_schema",
    "geometry_fingerprint_quantization_um",
    "selection_rank",
    "candidate_index",
    "target_rank",
    "target_bin_key",
    "target_recommended_new_samples",
    "inside_target_bin",
    "inside_pairwise_target_bin",
    "selection_score",
    "selection_source",
    "pairwise_priority_score",
    "pairwise_deficit_score",
    "marginal_deficit_score",
    "four_d_novelty_score",
    "acquisition_policy_authorized",
    "geometry_novelty_to_accepted",
    "geometry_diversity_cell",
    "random_selection_seed",
    "prediction_value_source",
    "prediction_calibration_sha256",
    "pred_neighbor_mean_distance",
    "pred_k_neighbors",
    "candidate_generation_mode",
    *tuple(f"pred_{feature}" for feature in PHYSICAL_FEATURE_FIELDS),
    *tuple(f"raw_pred_{feature}" for feature in PHYSICAL_FEATURE_FIELDS),
    *tuple(f"calibrated_pred_{feature}" for feature in PHYSICAL_FEATURE_FIELDS),
    *tuple(f"pred_uncertainty_{feature}" for feature in PHYSICAL_FEATURE_FIELDS),
    *tuple(f"target_{feature}" for feature in PHYSICAL_FEATURE_FIELDS),
    *tuple(f"target_{feature}_min" for feature in PHYSICAL_FEATURE_FIELDS),
    *tuple(f"target_{feature}_max" for feature in PHYSICAL_FEATURE_FIELDS),
)

QUEUE_FIELDS = (*GEOMETRY_FIELDS, *PROVENANCE_FIELDS)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    selection_csv = Path(args.selection_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    queue_csv = out_dir / "mars56_grounded_s4p_candidate_queue.csv"
    summary_path = out_dir / "mars56_grounded_s4p_candidate_queue_summary.json"
    report_path = out_dir / "mars56_grounded_s4p_candidate_queue_report.md"

    raw_rows = _read_csv(selection_csv)
    selected_rows = _select_rows(raw_rows, args)
    queue_rows, materialization_errors = _materialize_rows(selected_rows, args)
    errors = list(materialization_errors)
    identity_audit = _audit_queue_identity(queue_rows, args)
    errors.extend(identity_audit["errors"])
    if int(args.expected_count) >= 0 and len(queue_rows) != int(args.expected_count):
        errors.append(f"expected_count mismatch: rows={len(queue_rows)} expected={args.expected_count}")

    status = "PASS" if queue_rows and not errors else "FAIL"
    _write_csv(queue_csv, queue_rows, QUEUE_FIELDS)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "source_selection_csv": str(selection_csv),
        "candidate_csv": str(queue_csv),
        "sample_count": len(queue_rows),
        "input_row_count": len(raw_rows),
        "selected_input_row_count": len(selected_rows),
        "expected_count": int(args.expected_count),
        "field_order": list(QUEUE_FIELDS),
        "line_width_source": "candidate__geom__line_width_um preferred",
        "sync_primary_secondary_width_to_line_width": bool(args.sync_widths),
        "canonical_geometry_fields": list(CANONICAL_GEOMETRY_FIELDS),
        "geometry_fingerprint_schema": GEOMETRY_FINGERPRINT_SCHEMA,
        "geometry_fingerprint_quantization_um": float(args.geometry_fingerprint_quantization_um),
        "require_unique_geometry": bool(args.require_unique_geometry),
        "require_unique_source_id": bool(args.require_unique_source_id),
        "identity_audit": {key: value for key, value in identity_audit.items() if key != "errors"},
        "allow_pairwise_fallback": bool(args.allow_pairwise_fallback),
        "allow_random_exploration": bool(args.allow_random_exploration),
        "allow_geometry_diversity": bool(args.allow_geometry_diversity),
        "pairwise_fallback_input_count": sum(
            1 for row in selected_rows if str(row.get("selection_source")) == "pairwise_gap_fallback"
        ),
        "acquisition_policy_counts": {
            source: sum(1 for row in selected_rows if str(row.get("selection_source")) == source)
            for source in (
                "four_d_target_bin",
                "rare_marginal_real_seed",
                "pairwise_gap_fallback",
                "random_exploration",
                "geometry_diversity",
            )
        },
        "missing_or_invalid_field_count": len(materialization_errors),
        "error_count": len(errors),
        "errors": errors[:50],
        "note": "Converted from physical-feature targeted surrogate selection; predictions are not labels.",
        "prediction_provenance_preserved": True,
        "prediction_provenance_boundary": (
            "pred/raw_pred/calibrated_pred fields are carried only for post-EMX calibration audits; "
            "they do not affect geometry construction and are not simulator labels."
        ),
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")
    print(f"overall_status={status}")
    print(f"candidate_csv={queue_csv}")
    print(f"summary={summary_path}")
    print(f"report={report_path}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--max-count", type=int)
    parser.add_argument("--expected-count", type=int, default=-1)
    parser.add_argument("--candidate-id-prefix", default="m56s4p_tpilot_fix")
    parser.add_argument("--require-inside-target-bin", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--allow-pairwise-fallback",
        action="store_true",
        help="When strict inside-bin filtering is enabled, also accept rows explicitly inside an audited pairwise target bin.",
    )
    parser.add_argument(
        "--allow-random-exploration",
        action="store_true",
        help="Accept explicitly authorized random-exploration rows from an audited acquisition-mix selector.",
    )
    parser.add_argument(
        "--allow-geometry-diversity",
        action="store_true",
        help="Accept explicitly authorized geometry-diversity rows from an audited acquisition-mix selector.",
    )
    parser.add_argument("--sync-widths", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--require-unique-geometry", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--require-unique-source-id", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--geometry-fingerprint-quantization-um",
        type=float,
        default=DEFAULT_GEOMETRY_FINGERPRINT_QUANTIZATION_UM,
        help="Explicit canonical geometry quantization in micrometers before SHA-256 hashing.",
    )
    parser.add_argument("--no-fail-exit", action="store_true")
    args = parser.parse_args(argv)
    if not math.isfinite(args.geometry_fingerprint_quantization_um) or args.geometry_fingerprint_quantization_um <= 0.0:
        parser.error("--geometry-fingerprint-quantization-um must be finite and positive")
    return args


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _select_rows(rows: list[dict[str, str]], args: argparse.Namespace) -> list[dict[str, str]]:
    selected = rows
    if bool(args.require_inside_target_bin):
        selected = [
            row
            for row in selected
            if _truthy(row.get("inside_target_bin", "true"))
            or (
                bool(args.allow_pairwise_fallback)
                and str(row.get("selection_source")) == "pairwise_gap_fallback"
                and _truthy(row.get("inside_pairwise_target_bin"))
            )
            or (
                bool(getattr(args, "allow_random_exploration", False))
                and str(row.get("selection_source")) == "random_exploration"
                and _truthy(row.get("acquisition_policy_authorized"))
            )
            or (
                bool(getattr(args, "allow_geometry_diversity", False))
                and str(row.get("selection_source")) == "geometry_diversity"
                and _truthy(row.get("acquisition_policy_authorized"))
            )
        ]
    if args.max_count is not None:
        selected = selected[: max(0, int(args.max_count))]
    return selected


def _materialize_rows(rows: list[dict[str, str]], args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[str]]:
    queue_rows: list[dict[str, Any]] = []
    errors: list[str] = []
    quantization_um = float(
        getattr(args, "geometry_fingerprint_quantization_um", DEFAULT_GEOMETRY_FINGERPRINT_QUANTIZATION_UM)
    )
    for idx, row in enumerate(rows):
        out: dict[str, Any] = {"candidate_id": f"{args.candidate_id_prefix}_{idx + 1:05d}"}
        out["source_candidate_id"] = row.get("candidate_id") or ""
        line_width = _field_value(row, "line_width_um")
        for field in GEOMETRY_FIELDS:
            if field == "line_width_um":
                value = line_width
            elif bool(args.sync_widths) and field in {"primary_width_um", "secondary_width_um"}:
                value = line_width
            else:
                value = _field_value(row, field)
            if value is None or not math.isfinite(value):
                errors.append(f"row {idx} missing/invalid {field}")
                value = ""
            out[field] = value
        fingerprint = _geometry_fingerprint(out, quantization_um)
        if fingerprint is None:
            errors.append(f"row {idx} cannot form canonical geometry fingerprint")
            fingerprint = ""
        out["geometry_fingerprint_sha256"] = fingerprint
        out["geometry_fingerprint_schema"] = GEOMETRY_FINGERPRINT_SCHEMA
        out["geometry_fingerprint_quantization_um"] = quantization_um
        for field in PROVENANCE_FIELDS:
            if field in out:
                continue
            out[field] = _provenance_value(row, field)
        queue_rows.append(out)
    return queue_rows, errors


def _geometry_fingerprint(row: dict[str, Any], quantization_um: float) -> str | None:
    if not math.isfinite(quantization_um) or quantization_um <= 0.0:
        return None
    quantum = Decimal(str(quantization_um))
    quantized = []
    for field in CANONICAL_GEOMETRY_FIELDS:
        value = _as_float(row.get(field))
        if value is None or not math.isfinite(value):
            return None
        integer = (Decimal(str(value)) / quantum).to_integral_value(rounding=ROUND_HALF_UP)
        quantized.append(int(integer))
    payload = {
        "schema": GEOMETRY_FINGERPRINT_SCHEMA,
        "quantization_um": format(quantum, "f"),
        "fields": list(CANONICAL_GEOMETRY_FIELDS),
        "quantized_values": quantized,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _audit_queue_identity(queue_rows: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    fingerprints: dict[str, list[str]] = {}
    source_ids: dict[str, list[str]] = {}
    missing_fingerprint_count = 0
    missing_source_id_count = 0
    for row in queue_rows:
        candidate_id = str(row.get("candidate_id") or "")
        fingerprint = str(row.get("geometry_fingerprint_sha256") or "")
        source_id = str(row.get("source_candidate_id") or "")
        if fingerprint:
            fingerprints.setdefault(fingerprint, []).append(candidate_id)
        else:
            missing_fingerprint_count += 1
        if source_id:
            source_ids.setdefault(source_id, []).append(candidate_id)
        else:
            missing_source_id_count += 1

    duplicate_geometry = {key: values for key, values in fingerprints.items() if len(values) > 1}
    duplicate_source_ids = {key: values for key, values in source_ids.items() if len(values) > 1}
    duplicate_geometry_count = sum(len(values) - 1 for values in duplicate_geometry.values())
    duplicate_source_id_count = sum(len(values) - 1 for values in duplicate_source_ids.values())
    errors: list[str] = []
    require_unique_geometry = bool(getattr(args, "require_unique_geometry", True))
    require_unique_source_id = bool(getattr(args, "require_unique_source_id", True))
    if missing_fingerprint_count:
        errors.append(f"missing canonical geometry fingerprint: count={missing_fingerprint_count}")
    if require_unique_geometry and duplicate_geometry_count:
        errors.append(
            "duplicate canonical geometry: "
            f"extra_rows={duplicate_geometry_count} duplicate_groups={len(duplicate_geometry)}"
        )
    if require_unique_source_id and missing_source_id_count:
        errors.append(f"missing source_candidate_id: count={missing_source_id_count}")
    if require_unique_source_id and duplicate_source_id_count:
        errors.append(
            "duplicate source_candidate_id: "
            f"extra_rows={duplicate_source_id_count} duplicate_groups={len(duplicate_source_ids)}"
        )
    return {
        "row_count": len(queue_rows),
        "unique_geometry_fingerprint_count": len(fingerprints),
        "duplicate_geometry_extra_row_count": duplicate_geometry_count,
        "duplicate_geometry_group_count": len(duplicate_geometry),
        "duplicate_geometry_examples": [
            {"geometry_fingerprint_sha256": key, "candidate_ids": values}
            for key, values in list(duplicate_geometry.items())[:10]
        ],
        "unique_source_candidate_id_count": len(source_ids),
        "missing_source_candidate_id_count": missing_source_id_count,
        "duplicate_source_candidate_id_extra_row_count": duplicate_source_id_count,
        "duplicate_source_candidate_id_group_count": len(duplicate_source_ids),
        "duplicate_source_candidate_id_examples": [
            {"source_candidate_id": key, "candidate_ids": values}
            for key, values in list(duplicate_source_ids.items())[:10]
        ],
        "missing_geometry_fingerprint_count": missing_fingerprint_count,
        "errors": errors,
    }


def _field_value(row: dict[str, str], field: str) -> float | None:
    for key in (f"candidate__geom__{field}", f"geom__{field}", f"candidate__{field}", field):
        value = _as_float(row.get(key))
        if value is not None:
            return value
    return None


def _provenance_value(row: dict[str, Any], field: str) -> Any:
    value = row.get(field)
    if value not in (None, ""):
        return value
    candidate_value = row.get(f"candidate__{field}")
    return "" if candidate_value is None else candidate_value


def _as_float(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _truthy(value: str | None) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "pass", "ok"}


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: tuple[str, ...]) -> None:
    fieldnames = ("candidate_id", *fields)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# MARS56 Grounded S4P Targeted Queue",
        "",
        f"- Status: `{summary['overall_status']}`",
        f"- Source selection CSV: `{summary['source_selection_csv']}`",
        f"- Candidate CSV: `{summary['candidate_csv']}`",
        f"- Rows: `{summary['sample_count']}`",
        f"- Width handling: `{summary['line_width_source']}`; sync widths = `{summary['sync_primary_secondary_width_to_line_width']}`",
        f"- Canonical geometry uniqueness: required = `{summary['require_unique_geometry']}`, duplicates = `{summary['identity_audit']['duplicate_geometry_extra_row_count']}`",
        f"- Source candidate ID uniqueness: required = `{summary['require_unique_source_id']}`, duplicates = `{summary['identity_audit']['duplicate_source_candidate_id_extra_row_count']}`",
        f"- Geometry fingerprint: `{summary['geometry_fingerprint_schema']}` at `{summary['geometry_fingerprint_quantization_um']}` um",
        f"- Prediction provenance preserved: `{summary['prediction_provenance_preserved']}`",
        "",
        "The prediction columns are acquisition provenance only. They are not EMX/HFSS/ADS labels and do not affect geometry construction.",
    ]
    if summary["errors"]:
        lines.extend(["", "## Errors", ""])
        lines.extend(f"- {item}" for item in summary["errors"])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
