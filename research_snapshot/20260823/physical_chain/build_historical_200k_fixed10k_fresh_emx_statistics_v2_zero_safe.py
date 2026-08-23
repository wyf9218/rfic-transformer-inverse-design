#!/usr/bin/env python3
"""Build preregistered full-scale-normalized reporting from frozen v1 EMX rows.

This is a no-clobber methodological successor.  It does not alter candidate
selection, GDS, DRC, EMX, or the hash-bound physical extraction performed by
the v1 generator.  Target-relative percentage errors remain secondary only;
the primary reporting frame uses fixed, data-independent feature spans.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


SCHEMA = "historical_200k_fixed10k_fresh_real_emx_statistics_v2_methodology_zero_safe_v2"
PREREG_SCHEMA = "historical_200k_fixed10k_fresh_emx_statistics_preregistration_v2"
FEATURES = ("lp_nh", "ls_nh", "qmin", "k_abs")
PANELS = ("legacy_k_le_0p8", "extension_k_gt_0p8")
SPANS = {"lp_nh": 2.5, "ls_nh": 2.5, "qmin": 20.0, "k_abs": 1.0}
UNITS = {"lp_nh": "nH", "ls_nh": "nH", "qmin": "dimensionless", "k_abs": "dimensionless"}
HISTOGRAM_EDGES_FRACTION = (
    0.0,
    0.025,
    0.05,
    0.075,
    0.10,
    0.15,
    0.20,
    0.30,
    0.50,
    1.00,
)
SECONDARY_PERCENT_EDGES = (
    0.0,
    2.5,
    5.0,
    10.0,
    20.0,
    30.0,
    50.0,
    75.0,
    100.0,
    200.0,
    500.0,
)
FULL_SCALE_GATE_FRACTION = 0.10
RELATIVE_DENOMINATOR_EPSILON = 1.0e-12
COLORS = {
    "blue": "#2F6B9A",
    "blue_dark": "#173B57",
    "gold": "#D59A24",
    "grid": "#D8DEE4",
    "ink": "#24292F",
    "neutral": "#7A828A",
}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    paths = {
        "v1_summary": Path(args.v1_summary).expanduser().resolve(),
        "v1_evaluated_rows": Path(args.v1_evaluated_rows).expanduser().resolve(),
        "v1_manifest": Path(args.v1_manifest).expanduser().resolve(),
        "preregistration": Path(args.preregistration_json).expanduser().resolve(),
    }
    expected = {
        "v1_summary": args.expected_v1_summary_sha256,
        "v1_evaluated_rows": args.expected_v1_evaluated_rows_sha256,
        "v1_manifest": args.expected_v1_manifest_sha256,
        "preregistration": args.expected_preregistration_sha256,
    }
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        raise SystemExit(f"refusing to overwrite output directory: {out_dir}")

    source_records = {name: _file_record(path) for name, path in paths.items()}
    checks: dict[str, bool] = {}
    for name, record in source_records.items():
        checks[f"{name}_exists_nonzero"] = bool(record["exists"] and record["size_bytes"] > 0)
        checks[f"{name}_sha256_exact"] = record["sha256"] == expected[name]
    if not all(checks.values()):
        raise SystemExit(f"source hash gate failed: {_failed(checks)}")

    prereg = _read_json(paths["preregistration"])
    summary_v1 = _read_json(paths["v1_summary"])
    manifest_v1 = _read_json(paths["v1_manifest"])
    rows_v1 = _read_csv(paths["v1_evaluated_rows"])
    checks.update(_validate_preregistration(prereg))
    expected_count = int(args.expected_count)
    v1_artifact = (summary_v1.get("artifacts") or {}).get("fresh_emx_evaluated_rows") or {}
    checks.update(
        {
            "v1_summary_pass": summary_v1.get("overall_status") == "PASS",
            "v1_schema_expected": summary_v1.get("schema")
            == "historical_200k_fixed10k_fresh_real_emx_statistics_zero_safe_v3",
            "v1_original_denominator_10000": _integer(
                (summary_v1.get("funnel") or {}).get("original_target_denominator")
            )
            == 10000,
            "v1_expected_physical_count": _integer(
                (summary_v1.get("funnel") or {}).get("fresh_real_emx_evaluated")
            )
            == expected_count,
            "v1_rows_exact_count": len(rows_v1) == expected_count,
            "v1_summary_row_artifact_hash_matches": str(v1_artifact.get("sha256") or "")
            == expected["v1_evaluated_rows"],
            "v1_summary_row_artifact_count_matches": _integer(v1_artifact.get("row_count"))
            == expected_count,
            "v1_manifest_nonempty": bool(manifest_v1.get("artifacts")),
        }
    )
    if not all(checks.values()):
        raise SystemExit(f"v1/preregistration gate failed: {_failed(checks)}")

    rows_v2 = [_convert_row(row) for row in rows_v1]
    identities = [str(row["candidate_id_sha256"]).lower() for row in rows_v2]
    target_ids = [str(row["target_id"]) for row in rows_v2]
    checks.update(
        {
            "candidate_ids_unique": len(set(identities)) == expected_count,
            "target_ids_unique": len(set(target_ids)) == expected_count,
            "all_primary_numbers_finite": all(_row_primary_finite(row) for row in rows_v2),
            "q_floor_semantics_exact": all(
                bool(row["v2_q_floor_pass"])
                == (float(row["real_emx__qmin"]) >= float(row["target__qmin"]))
                for row in rows_v2
            ),
        }
    )
    if not all(checks.values()):
        raise SystemExit(f"row conversion gate failed: {_failed(checks)}")

    metrics = {
        "overall": _group_metrics(rows_v2),
        **{
            panel: _group_metrics([row for row in rows_v2 if row.get("panel") == panel])
            for panel in PANELS
        },
    }
    histogram_rows = _histogram_rows(rows_v2)
    secondary_histogram_rows = _secondary_histogram_rows(rows_v2)

    out_dir.mkdir(parents=True)
    rows_path = out_dir / "historical_200k_fresh_emx_v2_methodology_rows.csv"
    histogram_csv = out_dir / "fixed_full_scale_histogram_counts.csv"
    secondary_histogram_csv = out_dir / "fixed_secondary_percent_histogram_counts.csv"
    target_histogram = out_dir / "target_fixed_full_scale_histograms.png"
    proxy_histogram = out_dir / "proxy_fixed_full_scale_histograms.png"
    joint_histogram = out_dir / "joint_engineering_fixed_full_scale_histogram.png"
    feature_summary_chart = out_dir / "feature_error_primary_summary.png"
    panel_chart = out_dir / "legacy_extension_fixed_frame_percentiles.png"
    proxy_identity_chart = out_dir / "proxy_vs_emx_identity_and_residuals.png"
    funnel_chart = out_dir / "end_to_end_10000_funnel.png"
    summary_path = out_dir / "historical_200k_fresh_emx_statistics_v2_methodology_summary.json"
    manifest_path = out_dir / "artifact_sha256_manifest.json"
    _write_csv(rows_path, rows_v2)
    _write_csv(histogram_csv, histogram_rows)
    _write_csv(secondary_histogram_csv, secondary_histogram_rows)
    _plot_fixed_histograms(histogram_rows, "target_primary", target_histogram)
    _plot_fixed_histograms(histogram_rows, "proxy_exact", proxy_histogram)
    _plot_joint_histogram(histogram_rows, joint_histogram)
    _plot_feature_summary(metrics, feature_summary_chart)
    _plot_panel_percentiles(metrics, panel_chart)
    _plot_proxy_identity(rows_v2, proxy_identity_chart)
    _plot_funnel(summary_v1, metrics, funnel_chart)

    summary = {
        "schema": SCHEMA,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": "PASS",
        "decision": "USE_V2_PRIMARY_FOR_FINAL_REPORT_KEEP_V1_AS_SECONDARY",
        "scientific_scope": (
            "This successor changes reporting statistics only. Candidate generation, GDS, Calibre, "
            "fresh EMX, port mapping, and physical feature extraction remain the hash-bound v1 evidence."
        ),
        "method_preregistration_sha256": expected["preregistration"],
        "primary_method": {
            "feature_spans": SPANS,
            "feature_units": UNITS,
            "target_error_orientation": "fresh_emx_minus_target",
            "proxy_error_orientation": "proxy_minus_fresh_emx",
            "q_target_semantics": "hard floor; primary Q error is max(target_qmin-emx_qmin,0)",
            "joint_target_error": (
                "RMS across Lp/Ls/|K| fixed-span signed errors and one-sided Q-floor shortfall"
            ),
            "joint_proxy_error": "RMS across four fixed-span exact proxy-minus-EMX errors",
            "primary_composite_gate": (
                "|Lp error|<=10% of 2.5nH, |Ls error|<=10% of 2.5nH, "
                "||K| error|<=10% of 1.0, and Qmin>=target Q floor"
            ),
            "full_scale_gate_fraction": FULL_SCALE_GATE_FRACTION,
        },
        "secondary_diagnostics": {
            "lp_ls_target_relative_ape": "secondary only",
            "q_exact_deviation": "secondary only; Q acceptance is floor-based",
            "k_target_relative_ape": (
                "diagnostic only; unstable near zero and forbidden as a primary metric or gate"
            ),
            "legacy_v1_target_relative_10pct_gate": "retained per row as secondary only",
            "k_denominator_warning": (
                "Always label K target-relative APE denominator-sensitive; per-row target/EMX "
                "denominators are retained and exact zero is excluded."
            ),
            "relative_denominator_epsilon": RELATIVE_DENOMINATOR_EPSILON,
        },
        "histogram_contract": {
            "data_independent_edges_fraction_of_full_scale": list(HISTOGRAM_EDGES_FRACTION),
            "overflow": ">=100% of full scale is an explicit final bin",
            "result_dependent_clipping_or_p99_axis": False,
        },
        "funnel": summary_v1.get("funnel"),
        "v2_primary_report_guardrail": {
            "physically_evaluated_denominator": len(rows_v2),
            "pass_count": metrics["overall"]["composite_gates"][
                "primary_full_scale_10pct_plus_q_floor"
            ]["pass_count"],
            "pass_rate_among_physically_evaluated": metrics["overall"]["composite_gates"][
                "primary_full_scale_10pct_plus_q_floor"
            ]["pass_rate"],
            "pass_rate_of_original_10000": metrics["overall"]["composite_gates"][
                "primary_full_scale_10pct_plus_q_floor"
            ]["pass_count"]
            / 10000.0,
        },
        "inference_boundary": prereg.get("inference_boundary"),
        "row_count": len(rows_v2),
        "metrics": metrics,
        "checks": checks,
        "sources": source_records,
        "artifacts": {},
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary["artifacts"] = {
        "v2_rows": _file_record(rows_path, len(rows_v2)),
        "fixed_histogram_counts": _file_record(histogram_csv, len(histogram_rows)),
        "fixed_secondary_percent_histogram_counts": _file_record(
            secondary_histogram_csv, len(secondary_histogram_rows)
        ),
        "target_fixed_histograms": _file_record(target_histogram),
        "proxy_fixed_histograms": _file_record(proxy_histogram),
        "joint_engineering_fixed_histogram": _file_record(joint_histogram),
        "feature_error_primary_summary": _file_record(feature_summary_chart),
        "legacy_extension_fixed_frame_percentiles": _file_record(panel_chart),
        "proxy_vs_emx_identity_and_residuals": _file_record(proxy_identity_chart),
        "end_to_end_10000_funnel": _file_record(funnel_chart),
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    manifest = {
        "schema": "historical_200k_fixed10k_statistics_v2_artifact_manifest_v1",
        "artifacts": {
            "summary": _file_record(summary_path),
            **summary["artifacts"],
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("overall_status=PASS")
    print(f"row_count={len(rows_v2)}")
    print(f"summary={summary_path}")
    print(f"manifest={manifest_path}")
    return 0


def _convert_row(source: dict[str, str]) -> dict[str, Any]:
    output: dict[str, Any] = {
        "target_id": source.get("target_id", ""),
        "panel": source.get("panel", ""),
        "inside_historical_training_contract": source.get("inside_historical_training_contract", ""),
        "source_row_index": source.get("source_row_index", ""),
        "candidate_id_sha256": source.get("candidate_id_sha256", ""),
        "candidate_geometry_identity_sha256": source.get("candidate_geometry_identity_sha256", ""),
        "touchstone_sha256": source.get("touchstone_sha256", ""),
        "gds_timestamp_normalized_sha256": source.get("gds_timestamp_normalized_sha256", ""),
    }
    primary_components: list[float] = []
    exact_components: list[float] = []
    proxy_components: list[float] = []
    full_scale_gate_parts: list[bool] = []
    for feature in FEATURES:
        target = _required_finite(source, f"target__{feature}")
        proxy = _required_finite(source, f"proxy__{feature}")
        emx = _required_finite(source, f"real_emx__{feature}")
        span = SPANS[feature]
        target_error = emx - target
        proxy_error = proxy - emx
        target_norm = target_error / span
        proxy_norm = proxy_error / span
        output[f"target__{feature}"] = target
        output[f"proxy__{feature}"] = proxy
        output[f"real_emx__{feature}"] = emx
        output[f"v2_span__{feature}"] = span
        output[f"v2_target_error__{feature}"] = target_error
        output[f"v2_target_absolute_error__{feature}"] = abs(target_error)
        output[f"v2_target_range_normalized_error__{feature}"] = target_norm
        output[f"v2_target_absolute_range_normalized_error__{feature}"] = abs(target_norm)
        output[f"v2_proxy_minus_emx__{feature}"] = proxy_error
        output[f"v2_proxy_absolute_error_vs_emx__{feature}"] = abs(proxy_error)
        output[f"v2_proxy_range_normalized_error_vs_emx__{feature}"] = proxy_norm
        output[f"v2_proxy_absolute_range_normalized_error_vs_emx__{feature}"] = abs(proxy_norm)
        proxy_components.append(proxy_norm)
        exact_components.append(target_norm)
        if feature == "qmin":
            shortfall = max(target - emx, 0.0)
            shortfall_norm = shortfall / span
            output["v2_q_floor_pass"] = emx >= target
            output["v2_q_floor_shortfall"] = shortfall
            output["v2_q_floor_shortfall_range_normalized"] = shortfall_norm
            output["v2_q_floor_shortfall_percent_secondary"] = _relative_percent(
                shortfall, target
            )
            output["v2_q_exact_deviation_secondary"] = target_error
            output["v2_q_exact_range_normalized_deviation_secondary"] = target_norm
            primary_components.append(shortfall_norm)
        else:
            primary_components.append(target_norm)
        if feature in {"lp_nh", "ls_nh"}:
            output[f"v2_target_relative_ape_secondary__{feature}"] = _relative_percent(
                target_error, target
            )
            output[f"v2_proxy_relative_ape_secondary__{feature}"] = _relative_percent(
                proxy_error, emx
            )
        elif feature == "k_abs":
            output["v2_target_k_relative_ape_diagnostic"] = _relative_percent(target_error, target)
            output["v2_proxy_k_relative_ape_diagnostic"] = _relative_percent(proxy_error, emx)
            output["v2_target_k_ape_denominator"] = abs(target)
            output["v2_proxy_k_ape_emx_denominator"] = abs(emx)
            output["v2_k_ape_role_warning"] = "DENOMINATOR_SENSITIVE_DIAGNOSTIC_ONLY"
        if feature in {"lp_nh", "ls_nh", "k_abs"}:
            full_scale_gate_parts.append(abs(target_norm) <= FULL_SCALE_GATE_FRACTION)

    output["v2_target_joint_range_normalized_error_primary"] = float(
        math.sqrt(sum(value * value for value in primary_components) / len(primary_components))
    )
    output["v2_target_joint_range_normalized_error_exact_q_secondary"] = float(
        math.sqrt(sum(value * value for value in exact_components) / len(exact_components))
    )
    output["v2_proxy_joint_range_normalized_error_vs_emx"] = float(
        math.sqrt(sum(value * value for value in proxy_components) / len(proxy_components))
    )
    output["v2_primary_full_scale_10pct_plus_q_floor_gate"] = bool(
        all(full_scale_gate_parts) and output["v2_q_floor_pass"]
    )
    output["v2_secondary_legacy_relative_10pct_gate"] = _truthy(
        source.get("all_report_gates_pass")
    )
    return output


def _group_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("metric group is empty")
    result: dict[str, Any] = {"row_count": len(rows), "features": {}}
    for feature in FEATURES:
        target_error = np.asarray([float(row[f"v2_target_error__{feature}"]) for row in rows])
        target_norm = np.asarray(
            [float(row[f"v2_target_range_normalized_error__{feature}"]) for row in rows]
        )
        proxy_error = np.asarray([float(row[f"v2_proxy_minus_emx__{feature}"]) for row in rows])
        proxy_norm = np.asarray(
            [float(row[f"v2_proxy_range_normalized_error_vs_emx__{feature}"]) for row in rows]
        )
        target_payload = _error_metrics(target_error, target_norm)
        proxy_payload = _error_metrics(proxy_error, proxy_norm)
        if feature in {"lp_nh", "ls_nh"}:
            target_payload["target_relative_ape_secondary"] = _optional_percent_metrics(
                rows, f"v2_target_relative_ape_secondary__{feature}"
            )
            proxy_payload["relative_ape_secondary"] = _optional_percent_metrics(
                rows, f"v2_proxy_relative_ape_secondary__{feature}"
            )
        elif feature == "qmin":
            shortfall = np.asarray([float(row["v2_q_floor_shortfall"]) for row in rows])
            shortfall_norm = np.asarray(
                [float(row["v2_q_floor_shortfall_range_normalized"]) for row in rows]
            )
            target_payload["primary_floor"] = {
                "pass_count": sum(bool(row["v2_q_floor_pass"]) for row in rows),
                "pass_rate": sum(bool(row["v2_q_floor_pass"]) for row in rows) / len(rows),
                "shortfall": _nonnegative_metrics(shortfall),
                "shortfall_range_normalized": _nonnegative_metrics(shortfall_norm),
            }
            target_payload["floor_shortfall_percent_secondary"] = _optional_percent_metrics(
                rows, "v2_q_floor_shortfall_percent_secondary"
            )
            target_payload["exact_deviation_secondary"] = _error_metrics(target_error, target_norm)
        else:
            target_payload["target_relative_ape_diagnostic_only"] = _optional_percent_metrics(
                rows, "v2_target_k_relative_ape_diagnostic"
            )
            target_payload["denominator_abs_summary"] = _nonnegative_metrics(
                np.asarray([float(row["v2_target_k_ape_denominator"]) for row in rows])
            )
            target_payload["role_warning"] = "DENOMINATOR_SENSITIVE_DIAGNOSTIC_ONLY"
            proxy_payload["relative_ape_diagnostic_only"] = _optional_percent_metrics(
                rows, "v2_proxy_k_relative_ape_diagnostic"
            )
            proxy_payload["denominator_abs_summary"] = _nonnegative_metrics(
                np.asarray([float(row["v2_proxy_k_ape_emx_denominator"]) for row in rows])
            )
            proxy_payload["role_warning"] = "DENOMINATOR_SENSITIVE_DIAGNOSTIC_ONLY"
        result["features"][feature] = {
            "span": SPANS[feature],
            "unit": UNITS[feature],
            "target_vs_emx": target_payload,
            "proxy_vs_emx": proxy_payload,
        }
    result["joint"] = {
        "target_primary_floor_semantics": _nonnegative_metrics(
            np.asarray(
                [float(row["v2_target_joint_range_normalized_error_primary"]) for row in rows]
            )
        ),
        "target_exact_q_secondary": _nonnegative_metrics(
            np.asarray(
                [
                    float(row["v2_target_joint_range_normalized_error_exact_q_secondary"])
                    for row in rows
                ]
            )
        ),
        "proxy_vs_emx_exact": _nonnegative_metrics(
            np.asarray(
                [float(row["v2_proxy_joint_range_normalized_error_vs_emx"]) for row in rows]
            )
        ),
    }
    primary_pass = sum(bool(row["v2_primary_full_scale_10pct_plus_q_floor_gate"]) for row in rows)
    legacy_pass = sum(bool(row["v2_secondary_legacy_relative_10pct_gate"]) for row in rows)
    result["composite_gates"] = {
        "primary_full_scale_10pct_plus_q_floor": {
            "pass_count": primary_pass,
            "pass_rate": primary_pass / len(rows),
        },
        "secondary_legacy_target_relative_10pct": {
            "pass_count": legacy_pass,
            "pass_rate": legacy_pass / len(rows),
        },
    }
    return result


def _error_metrics(raw: np.ndarray, normalized: np.ndarray) -> dict[str, Any]:
    return {
        "raw": {
            "bias": float(np.mean(raw)),
            "mae": float(np.mean(np.abs(raw))),
            "rmse": float(np.sqrt(np.mean(np.square(raw)))),
            "absolute_error_quantiles": _quantiles(np.abs(raw)),
        },
        "fixed_span_normalized": {
            "bias": float(np.mean(normalized)),
            "mae": float(np.mean(np.abs(normalized))),
            "rmse": float(np.sqrt(np.mean(np.square(normalized)))),
            "absolute_error_quantiles": _quantiles(np.abs(normalized)),
        },
    }


def _nonnegative_metrics(values: np.ndarray) -> dict[str, Any]:
    return {
        "mean": float(np.mean(values)),
        "mae": float(np.mean(np.abs(values))),
        "rms": float(np.sqrt(np.mean(np.square(values)))),
        "rmse": float(np.sqrt(np.mean(np.square(values)))),
        "quantiles": _quantiles(values),
    }


def _quantiles(values: np.ndarray) -> dict[str, float]:
    return {
        "p50": float(np.quantile(values, 0.50)),
        "p90": float(np.quantile(values, 0.90)),
        "p95": float(np.quantile(values, 0.95)),
        "p99": float(np.quantile(values, 0.99)),
        "max": float(np.max(values)),
    }


def _optional_percent_metrics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    values = np.asarray(
        [float(row[key]) for row in rows if row.get(key) not in (None, "")], dtype=float
    )
    if not len(values):
        return {"valid_count": 0, "excluded_denominator_count": len(rows)}
    return {
        "valid_count": len(values),
        "excluded_denominator_count": len(rows) - len(values),
        "bias_percent": float(np.mean(values)),
        "mae_percent": float(np.mean(np.abs(values))),
        "rmse_percent": float(np.sqrt(np.mean(np.square(values)))),
        "absolute_percent_quantiles": _quantiles(np.abs(values)),
    }


def _histogram_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    edges = np.asarray((*HISTOGRAM_EDGES_FRACTION, np.inf), dtype=float)
    labels = [
        "0-2.5%",
        "2.5-5%",
        "5-7.5%",
        "7.5-10%",
        "10-15%",
        "15-20%",
        "20-30%",
        "30-50%",
        "50-100%",
        ">=100%",
    ]
    output: list[dict[str, Any]] = []
    groups = {"overall": rows, **{p: [row for row in rows if row.get("panel") == p] for p in PANELS}}
    for group_name, group_rows in groups.items():
        for family in ("target_primary", "proxy_exact"):
            for feature in FEATURES:
                if family == "target_primary" and feature == "qmin":
                    key = "v2_q_floor_shortfall_range_normalized"
                elif family == "target_primary":
                    key = f"v2_target_absolute_range_normalized_error__{feature}"
                else:
                    key = f"v2_proxy_absolute_range_normalized_error_vs_emx__{feature}"
                values = np.asarray([float(row[key]) for row in group_rows], dtype=float)
                counts, _ = np.histogram(values, bins=edges)
                for index, count in enumerate(counts):
                    output.append(
                        {
                            "group": group_name,
                            "family": family,
                            "feature": feature,
                            "bin_index": index,
                            "bin_label": labels[index],
                            "lower_fraction_inclusive": edges[index],
                            "upper_fraction_exclusive": "inf" if not math.isfinite(edges[index + 1]) else edges[index + 1],
                            "is_overflow": index == len(counts) - 1,
                            "count": int(count),
                            "fraction": int(count) / len(group_rows),
                            "denominator": len(group_rows),
                        }
                    )
        joint_values = np.asarray(
            [float(row["v2_target_joint_range_normalized_error_primary"]) for row in group_rows],
            dtype=float,
        )
        counts, _ = np.histogram(joint_values, bins=edges)
        for index, count in enumerate(counts):
            output.append(
                {
                    "group": group_name,
                    "family": "target_joint_engineering",
                    "feature": "joint_engineering",
                    "bin_index": index,
                    "bin_label": labels[index],
                    "lower_fraction_inclusive": edges[index],
                    "upper_fraction_exclusive": (
                        "inf" if not math.isfinite(edges[index + 1]) else edges[index + 1]
                    ),
                    "is_overflow": index == len(counts) - 1,
                    "count": int(count),
                    "fraction": int(count) / len(group_rows),
                    "denominator": len(group_rows),
                }
            )
    return output


def _secondary_histogram_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    edges = np.asarray((*SECONDARY_PERCENT_EDGES, np.inf), dtype=float)
    labels = [
        "0-2.5%",
        "2.5-5%",
        "5-10%",
        "10-20%",
        "20-30%",
        "30-50%",
        "50-75%",
        "75-100%",
        "100-200%",
        "200-500%",
        ">=500%",
    ]
    specifications = (
        ("target", "lp_nh", "v2_target_relative_ape_secondary__lp_nh"),
        ("target", "ls_nh", "v2_target_relative_ape_secondary__ls_nh"),
        ("target", "qmin_floor_shortfall", "v2_q_floor_shortfall_percent_secondary"),
        ("target_k_diagnostic_only", "k_abs", "v2_target_k_relative_ape_diagnostic"),
        ("proxy", "lp_nh", "v2_proxy_relative_ape_secondary__lp_nh"),
        ("proxy", "ls_nh", "v2_proxy_relative_ape_secondary__ls_nh"),
        ("proxy_k_diagnostic_only", "k_abs", "v2_proxy_k_relative_ape_diagnostic"),
    )
    output: list[dict[str, Any]] = []
    groups = {"overall": rows, **{p: [row for row in rows if row.get("panel") == p] for p in PANELS}}
    for group_name, group_rows in groups.items():
        for family, feature, key in specifications:
            values = np.asarray(
                [abs(float(row[key])) for row in group_rows if row.get(key) not in (None, "")],
                dtype=float,
            )
            counts, _ = np.histogram(values, bins=edges)
            for index, count in enumerate(counts):
                output.append(
                    {
                        "group": group_name,
                        "family": family,
                        "feature": feature,
                        "bin_index": index,
                        "bin_label": labels[index],
                        "lower_percent_inclusive": edges[index],
                        "upper_percent_exclusive": (
                            "inf" if not math.isfinite(edges[index + 1]) else edges[index + 1]
                        ),
                        "is_overflow": index == len(counts) - 1,
                        "count": int(count),
                        "fraction_of_valid_denominator": int(count) / len(values) if len(values) else None,
                        "valid_denominator": len(values),
                        "full_group_denominator": len(group_rows),
                        "role": "SECONDARY_DIAGNOSTIC_ONLY",
                    }
                )
    return output


def _plot_fixed_histograms(rows: list[dict[str, Any]], family: str, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    selected = [row for row in rows if row["group"] == "overall" and row["family"] == family]
    fig, axes = plt.subplots(2, 2, figsize=(12.8, 8.4), dpi=180)
    titles = {"lp_nh": "Lp", "ls_nh": "Ls", "qmin": "Qmin", "k_abs": "|K|"}
    for axis, feature in zip(axes.flat, FEATURES):
        feature_rows = [row for row in selected if row["feature"] == feature]
        x = np.arange(len(feature_rows))
        values = [100.0 * float(row["fraction"]) for row in feature_rows]
        colors = [COLORS["gold"] if bool(row["is_overflow"]) else COLORS["blue"] for row in feature_rows]
        axis.bar(x, values, color=colors, edgecolor=COLORS["blue_dark"], linewidth=0.5)
        axis.set_xticks(x, [str(row["bin_label"]) for row in feature_rows], rotation=30, ha="right")
        axis.set_ylabel("Rows (%)")
        q_note = " floor shortfall" if family == "target_primary" and feature == "qmin" else ""
        axis.set_title(f"{titles[feature]}{q_note}", loc="left")
        axis.grid(axis="y", color=COLORS["grid"], linewidth=0.6)
    title = (
        "Target error: preregistered fixed full-scale bins"
        if family == "target_primary"
        else "Proxy vs fresh EMX: preregistered fixed full-scale bins"
    )
    fig.suptitle(title, x=0.06, ha="left", fontsize=15, fontweight="bold", color=COLORS["ink"])
    fig.text(
        0.06,
        0.94,
        "Bins were frozen before results; >=100% full-scale is explicit overflow and no P99 clipping is used.",
        fontsize=9,
        color=COLORS["neutral"],
    )
    fig.tight_layout(rect=(0.03, 0.03, 0.99, 0.91))
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _plot_joint_histogram(rows: list[dict[str, Any]], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axis = plt.subplots(figsize=(10.8, 5.8), dpi=180)
    group_styles = (
        ("overall", COLORS["blue_dark"], "All physical survivors"),
        (PANELS[0], COLORS["blue"], "Legacy K<=0.8"),
        (PANELS[1], COLORS["gold"], "Extension K>0.8"),
    )
    width = 0.25
    for offset, (group, color, label) in enumerate(group_styles):
        selected = [
            row
            for row in rows
            if row["group"] == group and row["family"] == "target_joint_engineering"
        ]
        x = np.arange(len(selected)) + (offset - 1) * width
        axis.bar(
            x,
            [100.0 * float(row["fraction"]) for row in selected],
            width=width,
            color=color,
            label=label,
        )
    selected = [
        row
        for row in rows
        if row["group"] == "overall" and row["family"] == "target_joint_engineering"
    ]
    axis.set_xticks(np.arange(len(selected)), [str(row["bin_label"]) for row in selected], rotation=25, ha="right")
    axis.set_ylabel("Rows (%)")
    axis.set_title("Per-row joint engineering error on frozen full-scale frame", loc="left", fontsize=14)
    axis.text(
        0,
        1.02,
        "Q contributes one-sided floor shortfall; final overflow bin is explicit",
        transform=axis.transAxes,
        color=COLORS["neutral"],
    )
    axis.grid(axis="y", color=COLORS["grid"])
    axis.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _plot_feature_summary(metrics: dict[str, Any], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    overall = metrics["overall"]["features"]
    labels = ["Lp", "Ls", "Qmin floor", "|K|"]
    mae: list[float] = []
    rmse: list[float] = []
    for feature in FEATURES:
        target = overall[feature]["target_vs_emx"]
        if feature == "qmin":
            payload = target["primary_floor"]["shortfall_range_normalized"]
            mae.append(100.0 * float(payload["mae"]))
            rmse.append(100.0 * float(payload["rmse"]))
        else:
            payload = target["fixed_span_normalized"]
            mae.append(100.0 * float(payload["mae"]))
            rmse.append(100.0 * float(payload["rmse"]))
    x = np.arange(len(FEATURES))
    width = 0.34
    fig, axis = plt.subplots(figsize=(9.8, 5.8), dpi=180)
    axis.bar(x - width / 2, mae, width, label="MAE", color=COLORS["blue"])
    axis.bar(x + width / 2, rmse, width, label="RMSE", color=COLORS["gold"])
    axis.set_xticks(x, labels)
    axis.set_ylabel("Percent of frozen full-scale span")
    axis.set_title("Primary feature error summary", loc="left", fontsize=14)
    axis.text(
        0,
        1.02,
        "Spans: Lp=2.5nH, Ls=2.5nH, Q=20, K=1; Q uses floor shortfall",
        transform=axis.transAxes,
        color=COLORS["neutral"],
    )
    axis.grid(axis="y", color=COLORS["grid"])
    axis.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _plot_panel_percentiles(metrics: dict[str, Any], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(12.8, 8.4), dpi=180)
    labels = ["P50", "P90", "P95", "P99"]
    keys = ["p50", "p90", "p95", "p99"]
    x = np.arange(len(labels))
    width = 0.36
    for axis, feature in zip(axes.flat, FEATURES):
        values_by_panel: list[list[float]] = []
        for panel in PANELS:
            payload = metrics[panel]["features"][feature]["target_vs_emx"]
            if feature == "qmin":
                quantiles = payload["primary_floor"]["shortfall_range_normalized"]["quantiles"]
            else:
                quantiles = payload["fixed_span_normalized"]["absolute_error_quantiles"]
            values_by_panel.append([100.0 * float(quantiles[key]) for key in keys])
        axis.bar(x - width / 2, values_by_panel[0], width, color=COLORS["blue"], label="Legacy")
        axis.bar(x + width / 2, values_by_panel[1], width, color=COLORS["gold"], label="Extension")
        axis.set_xticks(x, labels)
        axis.set_ylabel("Percent of full scale")
        axis.set_title(feature + (" floor shortfall" if feature == "qmin" else ""), loc="left")
        axis.grid(axis="y", color=COLORS["grid"])
    handles, labels_out = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels_out, loc="upper right", frameon=False, ncol=2)
    fig.suptitle("Legacy versus extension: fixed-frame error percentiles", x=0.06, ha="left", fontsize=15)
    fig.tight_layout(rect=(0.03, 0.03, 0.99, 0.93))
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _plot_proxy_identity(rows: list[dict[str, Any]], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 4, figsize=(17.0, 8.0), dpi=180)
    for column, feature in enumerate(FEATURES):
        emx = np.asarray([float(row[f"real_emx__{feature}"]) for row in rows])
        proxy = np.asarray([float(row[f"proxy__{feature}"]) for row in rows])
        residual = proxy - emx
        top = axes[0, column]
        bottom = axes[1, column]
        top.scatter(emx, proxy, s=5, alpha=0.25, color=COLORS["blue"])
        low = min(float(np.min(emx)), float(np.min(proxy)))
        high = max(float(np.max(emx)), float(np.max(proxy)))
        top.plot([low, high], [low, high], color=COLORS["gold"], linewidth=1.2)
        top.set_xlabel("Fresh EMX")
        top.set_ylabel("Proxy")
        top.set_title(feature, loc="left")
        top.grid(color=COLORS["grid"], linewidth=0.5)
        bottom.scatter(emx, residual, s=5, alpha=0.25, color=COLORS["blue_dark"])
        bottom.axhline(0.0, color=COLORS["gold"], linewidth=1.2)
        bottom.set_xlabel("Fresh EMX")
        bottom.set_ylabel("Proxy - EMX")
        bottom.grid(color=COLORS["grid"], linewidth=0.5)
    fig.suptitle("Frozen forward proxy versus fresh EMX: identity and residual views", x=0.04, ha="left", fontsize=15)
    fig.tight_layout(rect=(0.02, 0.02, 0.99, 0.94))
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _plot_funnel(summary_v1: dict[str, Any], metrics: dict[str, Any], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    funnel = summary_v1.get("funnel") or {}
    labels = ["Targets", "Analytical", "Cadence", "Calibre", "Fresh EMX", "V2 guardrail"]
    primary_pass = int(
        metrics["overall"]["composite_gates"]["primary_full_scale_10pct_plus_q_floor"][
            "pass_count"
        ]
    )
    values = [
        10000,
        _integer(funnel.get("analytical_preflight_pass")),
        _integer(funnel.get("cadence_streamout_pass")),
        _integer(funnel.get("zero_blocking_calibre_pass")),
        _integer(funnel.get("fresh_real_emx_evaluated")),
        primary_pass,
    ]
    fig, axis = plt.subplots(figsize=(10.8, 5.8), dpi=180)
    x = np.arange(len(labels))
    axis.bar(x, values, color=[COLORS["blue_dark"], *([COLORS["blue"]] * 4), COLORS["gold"]])
    axis.set_xticks(x, labels, rotation=20, ha="right")
    axis.set_ylabel("Candidates (original denominator = 10,000)")
    axis.set_title("End-to-end physical evidence funnel", loc="left", fontsize=14)
    for index, value in enumerate(values):
        axis.text(index, value + max(values) * 0.012, f"{value:,}\n{100.0 * value / 10000.0:.1f}%", ha="center", fontsize=8)
    axis.grid(axis="y", color=COLORS["grid"])
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _validate_preregistration(payload: dict[str, Any]) -> dict[str, bool]:
    histogram = list(payload.get("fixed_histogram_bins_fraction_of_full_scale") or [])
    secondary = list(payload.get("fixed_secondary_percent_bins") or [])
    source_contracts = payload.get("source_contracts") or {}
    guardrail = payload.get("engineering_guardrail") or {}
    secondary_metrics = payload.get("secondary_metrics") or {}
    return {
        "prereg_schema_exact": payload.get("schema") == PREREG_SCHEMA,
        "prereg_frozen_before_results": payload.get("status")
        == "FROZEN_BEFORE_ANY_FRESH_EMX_RESULT",
        "prereg_spans_exact": payload.get("fixed_frame_spans") == SPANS,
        "prereg_histogram_edges_exact": histogram[:-1]
        == list(HISTOGRAM_EDGES_FRACTION)
        and histogram[-1:] == ["INF"],
        "prereg_secondary_edges_exact": secondary[:-1] == list(SECONDARY_PERCENT_EDGES)
        and secondary[-1:] == ["INF"],
        "prereg_guardrail_exact": (
            guardrail.get("label")
            == "PREDECLARED_10_PERCENT_OF_FULL_SCALE_REPORTING_GUARDRAIL_NOT_FOUNDRY_SPEC"
            and "0.10" in str(guardrail.get("lp_pass"))
            and "0.10" in str(guardrail.get("ls_pass"))
            and "0.10" in str(guardrail.get("k_pass"))
            and guardrail.get("q_pass") == "achieved_qmin >= target_qmin"
        ),
        "prereg_v1_secondary_only": "SECONDARY_LEGACY_DIAGNOSTIC"
        in str(secondary_metrics.get("v1_target_relative_10pct_composite")),
        "prereg_v1_generator_pin": source_contracts.get("v1_statistics_generator_sha256")
        == "b57c27e81b1ebfad892f60f3d9831d2f98d877f048b4c8d257b81410840db843",
        "prereg_extractor_pin": source_contracts.get("physical_extractor_sha256")
        == "cce7b4cf81e2ad981e0c0c5ad71b170c47ddd06ac7cd254b6384497b47aba107",
        "prereg_metric_helper_pin": source_contracts.get("metric_helper_sha256")
        == "4d934ae7d9119dcc33f598ec9450f9c0664722d45c22b541418e1cb63d0f3350",
    }


def _row_primary_finite(row: dict[str, Any]) -> bool:
    keys = [
        *(f"v2_target_error__{f}" for f in FEATURES),
        *(f"v2_target_range_normalized_error__{f}" for f in FEATURES),
        *(f"v2_proxy_minus_emx__{f}" for f in FEATURES),
        *(f"v2_proxy_range_normalized_error_vs_emx__{f}" for f in FEATURES),
        "v2_q_floor_shortfall",
        "v2_q_floor_shortfall_range_normalized",
        "v2_target_joint_range_normalized_error_primary",
        "v2_target_joint_range_normalized_error_exact_q_secondary",
        "v2_proxy_joint_range_normalized_error_vs_emx",
    ]
    return all(math.isfinite(float(row[key])) for key in keys)


def _relative_percent(numerator: float, denominator: float) -> float | None:
    if abs(denominator) <= RELATIVE_DENOMINATOR_EPSILON:
        return None
    return 100.0 * numerator / abs(denominator)


def _required_finite(row: dict[str, str], key: str) -> float:
    try:
        value = float(row[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"missing numeric value for {key}") from exc
    if not math.isfinite(value):
        raise ValueError(f"non-finite numeric value for {key}")
    return value


def _integer(value: Any) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"missing integer value: {value!r}") from exc
    if not math.isfinite(number) or not math.isclose(
        number, round(number), rel_tol=0.0, abs_tol=1.0e-9
    ):
        raise ValueError(f"invalid integer value: {value!r}")
    return int(round(number))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v1-summary", required=True)
    parser.add_argument("--v1-evaluated-rows", required=True)
    parser.add_argument("--v1-manifest", required=True)
    parser.add_argument("--preregistration-json", required=True)
    parser.add_argument("--expected-v1-summary-sha256", required=True)
    parser.add_argument("--expected-v1-evaluated-rows-sha256", required=True)
    parser.add_argument("--expected-v1-manifest-sha256", required=True)
    parser.add_argument("--expected-preregistration-sha256", required=True)
    parser.add_argument("--expected-count", required=True, type=int)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args(argv)
    for name in (
        "expected_v1_summary_sha256",
        "expected_v1_evaluated_rows_sha256",
        "expected_v1_manifest_sha256",
        "expected_preregistration_sha256",
    ):
        value = str(getattr(args, name)).lower()
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            parser.error(f"--{name.replace('_', '-')} must be SHA256")
        setattr(args, name, value)
    if args.expected_count < 1:
        parser.error("--expected-count must be positive")
    return args


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty CSV: {path}")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_record(path: Path, row_count: int | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "path": str(path),
        "exists": path.is_file(),
        "size_bytes": path.stat().st_size if path.is_file() else 0,
        "sha256": _sha256(path) if path.is_file() else "",
    }
    if row_count is not None:
        record["row_count"] = row_count
    return record


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "pass", "ok"}


def _failed(checks: dict[str, bool]) -> list[str]:
    return [name for name, passed in checks.items() if not passed]


if __name__ == "__main__":
    raise SystemExit(main())
