#!/usr/bin/env python3
"""Audit that a MARS56 S4P production queue is physical-feature targeted.

The 100k production runner must not spend days on a geometry-only space-filling
queue.  This audit checks the candidate queue before EMX is launched and
requires traceable evidence that the queue came from the physical-feature
target-bin selector/materializer.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
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

PHYSICAL_FEATURE_ALIASES = {
    "lp": {"lp", "lpn", "lpnh", "lpnhcenter", "lp_nh", "lp_nh_center", "lpnhvalue", "lp_nh_value"},
    "ls": {"ls", "lsn", "lsnh", "lsnhcenter", "ls_nh", "ls_nh_center", "lsnhvalue", "ls_nh_value"},
    "q": {"q", "qcenter", "q_center", "qvalue", "qfactor", "qualityfactor"},
    "k": {
        "k",
        "kw",
        "abs_k",
        "abs_k_center",
        "absk",
        "abskcenter",
        "k_abs",
        "k_abs_center",
        "kabs",
        "kabscenter",
        "k_magnitude",
        "k_magnitude_center",
        "coupling",
        "couplingfactor",
    },
}


def main() -> int:
    args = _parse_args()
    candidate_csv = Path(args.candidate_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    candidate_rows, candidate_fields = _read_csv(candidate_csv)
    candidate_summary_path = _resolve_candidate_summary(candidate_csv, args.candidate_summary)
    candidate_summary = _read_json(candidate_summary_path)
    selection_csv = _resolve_selection_csv(candidate_summary, args.selection_csv)
    selection_rows, selection_fields = _read_csv(selection_csv) if selection_csv is not None else ([], [])
    selection_summary_path = _resolve_selection_summary(selection_csv, args.selection_summary)
    selection_summary = _read_json(selection_summary_path)

    checks: list[dict[str, Any]] = []
    checks.append(_check("candidate_csv_exists", candidate_csv.is_file(), str(candidate_csv)))
    checks.append(_check("candidate_rows_present", bool(candidate_rows), f"rows={len(candidate_rows)}"))
    if args.expected_count is not None:
        checks.append(
            _check(
                "candidate_rows_meet_expected_count",
                len(candidate_rows) >= int(args.expected_count),
                f"rows={len(candidate_rows)}, expected={args.expected_count}",
            )
        )
    checks.append(_check("geometry_columns_present", _has_geometry_columns(candidate_fields), candidate_fields))
    candidate_identity = _candidate_identity_audit(candidate_rows, candidate_fields, candidate_summary)
    checks.extend(candidate_identity["checks"])
    checks.append(_check("candidate_summary_exists", candidate_summary_path is not None and candidate_summary_path.is_file(), str(candidate_summary_path)))
    if candidate_summary:
        checks.extend(_candidate_summary_checks(candidate_summary, candidate_csv, args.expected_count))
    else:
        checks.append(_check("candidate_summary_loaded", False, "missing or unreadable candidate summary"))

    geometry_only_reasons = _geometry_only_reasons(candidate_rows, candidate_summary)
    checks.append(
        _check(
            "reject_geometry_only_space_filling_queue",
            not geometry_only_reasons,
            geometry_only_reasons or "no geometry-only bootstrap markers found",
        )
    )

    checks.append(_check("source_selection_csv_declared", selection_csv is not None, str(selection_csv)))
    checks.append(_check("source_selection_csv_exists", selection_csv is not None and selection_csv.is_file(), str(selection_csv)))
    if args.expected_count is not None and selection_rows:
        checks.append(
            _check(
                "source_selection_rows_meet_expected_count",
                len(selection_rows) >= int(args.expected_count),
                f"rows={len(selection_rows)}, expected={args.expected_count}",
            )
        )
    physical_columns = _physical_feature_column_evidence(selection_fields)
    checks.append(_check("selection_has_predicted_physical_features", physical_columns["predicted_ok"], physical_columns["predicted"]))
    checks.append(_check("selection_has_target_physical_bins", physical_columns["target_ok"], physical_columns["target"]))
    checks.append(
        _check(
            "selection_target_gap_evidence",
            _target_gap_evidence(selection_rows),
            "at least one row is inside a full 4-D target bin or an explicitly identified pairwise fallback bin",
        )
    )
    if selection_summary:
        checks.extend(_selection_summary_checks(selection_summary, selection_csv, args.expected_count))

    status = "PASS" if all(item["pass"] for item in checks) else "FAIL"
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "RUN_EMX_PRODUCTION_QUEUE" if status == "PASS" else "STOP_BEFORE_EMX_QUEUE_NOT_PROVEN_PHYSICAL_TARGETED",
        "candidate_csv": str(candidate_csv),
        "candidate_summary": str(candidate_summary_path) if candidate_summary_path else None,
        "selection_csv": str(selection_csv) if selection_csv else None,
        "selection_summary": str(selection_summary_path) if selection_summary_path else None,
        "expected_count": args.expected_count,
        "candidate_row_count": len(candidate_rows),
        "selection_row_count": len(selection_rows),
        "candidate_csv_source": _file_source(candidate_csv),
        "selection_csv_source": _file_source(selection_csv) if selection_csv else None,
        "physical_feature_column_evidence": physical_columns,
        "geometry_only_reasons": geometry_only_reasons,
        "candidate_identity_evidence": {key: value for key, value in candidate_identity.items() if key != "checks"},
        "checks": checks,
        "limitations": [
            "This preflight proves queue provenance only; EMX labels and physical-feature uniformity are still verified after S-parameter generation.",
            "The candidate CSV may contain geometry columns only, but it must trace back to a physical-feature targeted selection CSV.",
        ],
    }
    summary_path = out_dir / "mars56_s4p_candidate_queue_provenance_summary.json"
    report_path = out_dir / "mars56_s4p_candidate_queue_provenance_report.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    print(f"summary={summary_path}")
    print(f"overall_status={status}")
    print(f"CANDIDATE_QUEUE_PROVENANCE_STATUS={status}")
    return 0 if status == "PASS" or args.no_fail_exit else 2


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--expected-count", type=int)
    parser.add_argument("--candidate-summary", help="Optional mars56_grounded_s4p_candidate_queue_summary.json path")
    parser.add_argument("--selection-csv", help="Optional physical_feature_targeted_candidate_selection.csv path")
    parser.add_argument("--selection-summary", help="Optional physical_feature_targeted_candidate_selection_summary.json path")
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args()


def _read_csv(path: Path | None) -> tuple[list[dict[str, str]], list[str]]:
    if path is None or not path.is_file():
        return [], []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = [dict(row) for row in reader]
        return rows, list(reader.fieldnames or [])


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _resolve_candidate_summary(candidate_csv: Path, explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser().resolve()
    default = candidate_csv.with_name("mars56_grounded_s4p_candidate_queue_summary.json")
    return default if default.exists() else default


def _resolve_selection_csv(candidate_summary: dict[str, Any], explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser().resolve()
    source = candidate_summary.get("source_selection_csv")
    if isinstance(source, str) and source.strip():
        return Path(source).expanduser().resolve()
    return None


def _resolve_selection_summary(selection_csv: Path | None, explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser().resolve()
    if selection_csv is None:
        return None
    default = selection_csv.with_name("physical_feature_targeted_candidate_selection_summary.json")
    return default if default.exists() else None


def _candidate_summary_checks(summary: dict[str, Any], candidate_csv: Path, expected_count: int | None) -> list[dict[str, Any]]:
    checks = [
        _check("candidate_summary_status_pass", summary.get("overall_status") == "PASS", summary.get("overall_status")),
        _check("candidate_summary_declares_source_selection", bool(summary.get("source_selection_csv")), summary.get("source_selection_csv")),
        _check(
            "candidate_summary_mentions_physical_feature_targeted",
            "physical-feature targeted" in json.dumps(summary, ensure_ascii=False).lower(),
            "summary should identify physical-feature targeted materialization",
        ),
    ]
    summary_csv = summary.get("candidate_csv")
    if isinstance(summary_csv, str) and summary_csv.strip():
        checks.append(
            _check(
                "candidate_summary_csv_matches_input",
                Path(summary_csv).expanduser().resolve() == candidate_csv,
                summary_csv,
            )
        )
    if expected_count is not None:
        sample_count = _as_int(summary.get("sample_count"))
        checks.append(
            _check(
                "candidate_summary_sample_count_meets_expected",
                sample_count is not None and sample_count >= int(expected_count),
                f"sample_count={sample_count}, expected={expected_count}",
            )
        )
    return checks


def _selection_summary_checks(summary: dict[str, Any], selection_csv: Path | None, expected_count: int | None) -> list[dict[str, Any]]:
    checks = [
        _check("selection_summary_status_pass", summary.get("overall_status") == "PASS", summary.get("overall_status")),
        _check(
            "selection_summary_decision_targeted",
            str(summary.get("decision", "")).startswith("USE_SELECTED_CANDIDATES"),
            summary.get("decision"),
        ),
    ]
    selected_csv = summary.get("selected_csv")
    if selection_csv is not None and isinstance(selected_csv, str) and selected_csv.strip():
        checks.append(
            _check(
                "selection_summary_csv_matches_source",
                Path(selected_csv).expanduser().resolve() == selection_csv,
                selected_csv,
            )
        )
    if expected_count is not None:
        selected_count = _as_int(summary.get("selected_candidate_count"))
        inside_count = _as_int(summary.get("selected_inside_target_bin_count"))
        pairwise_count = _as_int(summary.get("selected_pairwise_gap_count")) or 0
        effective_count = _as_int(summary.get("selected_inside_or_pairwise_target_count"))
        if effective_count is None:
            effective_count = (inside_count or 0) + pairwise_count
        mix = summary.get("acquisition_mix_contract")
        checks.append(
            _check(
                "selection_summary_selected_count_meets_expected",
                selected_count is not None and selected_count >= int(expected_count),
                f"selected={selected_count}, expected={expected_count}",
            )
        )
        if isinstance(mix, dict):
            requested_counts = mix.get("requested_counts") or {}
            selected_counts = mix.get("selected_counts") or {}
            policy_count = _as_int(summary.get("selected_policy_eligible_count"))
            checks.extend(
                [
                    _check(
                        "selection_summary_five_arm_policy_count_meets_expected",
                        policy_count is not None and policy_count >= int(expected_count),
                        f"policy_eligible={policy_count}, expected={expected_count}",
                    ),
                    _check(
                        "selection_summary_five_arm_exact_quotas",
                        requested_counts == selected_counts
                        and sum(int(value) for value in selected_counts.values()) == int(expected_count),
                        {"requested": requested_counts, "selected": selected_counts},
                    ),
                    _check(
                        "selection_summary_five_arm_disjoint_proxy_only",
                        mix.get("arms_are_disjoint") is True
                        and mix.get("proxy_values_are_acquisition_only") is True,
                        {
                            "arms_are_disjoint": mix.get("arms_are_disjoint"),
                            "proxy_values_are_acquisition_only": mix.get("proxy_values_are_acquisition_only"),
                        },
                    ),
                ]
            )
        else:
            checks.append(
                _check(
                    "selection_summary_targeted_count_meets_expected",
                    effective_count >= int(expected_count),
                    f"full4d_inside={inside_count}, pairwise={pairwise_count}, effective={effective_count}, expected={expected_count}",
                )
            )
    return checks


def _has_geometry_columns(fields: list[str]) -> bool:
    field_set = set(fields)
    for field in GEOMETRY_FIELDS:
        if field not in field_set and f"geom__{field}" not in field_set:
            return False
    return True


def _candidate_identity_audit(
    rows: list[dict[str, str]],
    fields: list[str],
    summary: dict[str, Any],
) -> dict[str, Any]:
    required_identity_fields = {
        "candidate_id",
        "source_candidate_id",
        "geometry_fingerprint_sha256",
        "geometry_fingerprint_schema",
        "geometry_fingerprint_quantization_um",
    }
    identity_columns_present = required_identity_fields.issubset(set(fields))
    summary_quantization = _finite(summary.get("geometry_fingerprint_quantization_um"))
    summary_contract_valid = (
        summary.get("geometry_fingerprint_schema") == GEOMETRY_FINGERPRINT_SCHEMA
        and summary_quantization is not None
        and summary_quantization > 0.0
        and summary.get("canonical_geometry_fields") == list(CANONICAL_GEOMETRY_FIELDS)
    )
    candidate_ids = [str(row.get("candidate_id") or "") for row in rows]
    source_ids = [str(row.get("source_candidate_id") or "") for row in rows]
    observed_fingerprints = [str(row.get("geometry_fingerprint_sha256") or "") for row in rows]
    candidate_ids_unique = bool(rows) and all(candidate_ids) and len(candidate_ids) == len(set(candidate_ids))
    source_ids_unique = bool(rows) and all(source_ids) and len(source_ids) == len(set(source_ids))
    sha_format_valid = bool(rows) and all(re.fullmatch(r"[0-9a-f]{64}", value) for value in observed_fingerprints)
    aliases_synchronized = True
    fingerprint_rows_match = bool(rows) and summary_contract_valid and identity_columns_present
    row_contract_matches = bool(rows) and summary_contract_valid and identity_columns_present
    recomputed_fingerprints: list[str] = []
    for row in rows:
        line_width = _finite(row.get("line_width_um"))
        primary_width = _finite(row.get("primary_width_um"))
        secondary_width = _finite(row.get("secondary_width_um"))
        if (
            line_width is None
            or primary_width is None
            or secondary_width is None
            or not math.isclose(line_width, primary_width, rel_tol=0.0, abs_tol=1.0e-12)
            or not math.isclose(line_width, secondary_width, rel_tol=0.0, abs_tol=1.0e-12)
        ):
            aliases_synchronized = False
        row_quantization = _finite(row.get("geometry_fingerprint_quantization_um"))
        if (
            row.get("geometry_fingerprint_schema") != GEOMETRY_FINGERPRINT_SCHEMA
            or row_quantization is None
            or summary_quantization is None
            or not math.isclose(row_quantization, summary_quantization, rel_tol=0.0, abs_tol=0.0)
        ):
            row_contract_matches = False
        recomputed = (
            _canonical_geometry_fingerprint(row, summary_quantization)
            if summary_quantization is not None and summary_quantization > 0.0
            else None
        )
        if recomputed is None:
            fingerprint_rows_match = False
        else:
            recomputed_fingerprints.append(recomputed)
            if recomputed != str(row.get("geometry_fingerprint_sha256") or ""):
                fingerprint_rows_match = False
    canonical_geometry_unique = (
        bool(rows)
        and len(recomputed_fingerprints) == len(rows)
        and len(recomputed_fingerprints) == len(set(recomputed_fingerprints))
    )
    summary_identity = summary.get("identity_audit") or {}
    summary_identity_pass = (
        summary.get("require_unique_geometry") is True
        and summary.get("require_unique_source_id") is True
        and int(summary_identity.get("duplicate_geometry_extra_row_count") or 0) == 0
        and int(summary_identity.get("duplicate_source_candidate_id_extra_row_count") or 0) == 0
        and int(summary_identity.get("missing_geometry_fingerprint_count") or 0) == 0
        and int(summary_identity.get("missing_source_candidate_id_count") or 0) == 0
        and int(summary_identity.get("unique_geometry_fingerprint_count") or 0) == len(rows)
        and int(summary_identity.get("unique_source_candidate_id_count") or 0) == len(rows)
    )
    checks = [
        _check("candidate_identity_columns_present", identity_columns_present, sorted(required_identity_fields)),
        _check("candidate_ids_nonempty_unique", candidate_ids_unique, f"rows={len(rows)}, unique={len(set(candidate_ids))}"),
        _check("source_candidate_ids_nonempty_unique", source_ids_unique, f"rows={len(rows)}, unique={len(set(source_ids))}"),
        _check("shared_line_width_aliases_synchronized", aliases_synchronized, "line_width=primary_width=secondary_width"),
        _check("geometry_fingerprint_summary_contract_valid", summary_contract_valid, summary_quantization),
        _check("geometry_fingerprint_row_contract_matches_summary", row_contract_matches, GEOMETRY_FINGERPRINT_SCHEMA),
        _check("geometry_fingerprint_sha256_format_valid", sha_format_valid, f"rows={len(rows)}"),
        _check("geometry_fingerprints_match_recomputed_canonical_geometry", fingerprint_rows_match, f"rows={len(rows)}"),
        _check("canonical_geometries_unique", canonical_geometry_unique, f"rows={len(rows)}, unique={len(set(recomputed_fingerprints))}"),
        _check("candidate_summary_identity_audit_pass", summary_identity_pass, summary_identity),
    ]
    return {
        "schema": GEOMETRY_FINGERPRINT_SCHEMA,
        "quantization_um": summary_quantization,
        "row_count": len(rows),
        "candidate_id_unique_count": len(set(candidate_ids)),
        "source_candidate_id_unique_count": len(set(source_ids)),
        "canonical_geometry_unique_count": len(set(recomputed_fingerprints)),
        "checks": checks,
    }


def _canonical_geometry_fingerprint(row: dict[str, str], quantization_um: float) -> str | None:
    if not math.isfinite(quantization_um) or quantization_um <= 0.0:
        return None
    quantum = Decimal(str(quantization_um))
    quantized = []
    for field in CANONICAL_GEOMETRY_FIELDS:
        value = _finite(row.get(field))
        if value is None:
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


def _geometry_only_reasons(rows: list[dict[str, str]], summary: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    decision = str(summary.get("decision", ""))
    if decision == "USE_GEOMETRY_QUEUE_FOR_MARS56_GROUNDED_S4P_EMX":
        reasons.append("candidate summary decision is geometry-only queue")
    if not summary.get("source_selection_csv"):
        reasons.append("candidate summary has no source_selection_csv")
    for row in rows[:50]:
        source = str(row.get("bootstrap_source", ""))
        if source == "geometry_space_filling_no_physical_labels":
            reasons.append("candidate CSV contains bootstrap_source=geometry_space_filling_no_physical_labels")
            break
    return reasons


def _physical_feature_column_evidence(fields: list[str]) -> dict[str, Any]:
    predicted = sorted(field for field in fields if field.startswith("pred_"))
    target = sorted(field for field in fields if field.startswith("target_"))
    predicted_categories = _feature_categories(predicted, prefix="pred_")
    target_categories = _feature_categories(target, prefix="target_")
    required = {"lp", "ls", "q", "k"}
    return {
        "predicted": predicted,
        "target": target,
        "predicted_categories": sorted(predicted_categories),
        "target_categories": sorted(target_categories),
        "predicted_ok": required.issubset(predicted_categories),
        "target_ok": required.issubset(target_categories),
    }


def _feature_categories(fields: list[str], *, prefix: str) -> set[str]:
    categories: set[str] = set()
    for field in fields:
        name = field.removeprefix(prefix)
        name = re.sub(r"_(min|max|target|value)$", "", name.lower())
        normalized = re.sub(r"[^a-z0-9]+", "", name)
        for category, aliases in PHYSICAL_FEATURE_ALIASES.items():
            if normalized in {re.sub(r"[^a-z0-9]+", "", item) for item in aliases}:
                categories.add(category)
            elif normalized.startswith(category) and category in {"lp", "ls"}:
                categories.add(category)
    return categories


def _target_gap_evidence(rows: list[dict[str, str]]) -> bool:
    if not rows:
        return False
    return any(
        _truthy(row.get("inside_target_bin"))
        or (
            str(row.get("selection_source")) == "pairwise_gap_fallback"
            and _truthy(row.get("inside_pairwise_target_bin"))
            and not _truthy(row.get("inside_target_bin"))
        )
        for row in rows
    )


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "pass", "ok"}


def _as_int(value: Any) -> int | None:
    try:
        out = int(float(value))
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _file_source(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    out: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    if not path.is_file():
        return out
    digest = hashlib.sha256()
    line_count = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            line_count += chunk.count(b"\n")
    out.update({"size_bytes": path.stat().st_size, "sha256": digest.hexdigest(), "line_count": line_count})
    return out


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": detail}


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# MARS56 S4P Candidate Queue Provenance Audit",
        "",
        f"- Overall status: `{summary['overall_status']}`",
        f"- Decision: `{summary['decision']}`",
        f"- Candidate CSV: `{summary['candidate_csv']}`",
        f"- Candidate rows: `{summary['candidate_row_count']}`",
        f"- Selection CSV: `{summary['selection_csv']}`",
        f"- Selection rows: `{summary['selection_row_count']}`",
        "",
        "## Checks",
        "",
    ]
    for item in summary["checks"]:
        mark = "PASS" if item["pass"] else "FAIL"
        lines.append(f"- {mark}: {item['name']} - {item['detail']}")
    lines.extend(["", "## Geometry-only rejection evidence", ""])
    if summary["geometry_only_reasons"]:
        lines.extend(f"- {item}" for item in summary["geometry_only_reasons"])
    else:
        lines.append("- No geometry-only bootstrap markers found.")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
