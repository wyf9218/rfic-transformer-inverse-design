#!/usr/bin/env python3
"""Freeze the historical-200k fixed-10k one-shot EMX readiness audit.

The utility never repairs, projects, ranks, or simulates a geometry.  It
replays the production analytical geometry/port-ground gate on every frozen
one-shot prediction, writes all failures into the denominator, and emits a
candidate queue only for rows that pass unchanged.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import inspect
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
for candidate in (SCRIPT_DIR, REPO_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import prepare_current_foundry_domain_pilot as identity  # noqa: E402
from rfic_transformer_inverse_design.api import (  # noqa: E402
    TransformerOptimizationAdapter,
    load_run_config,
)
from rfic_transformer_inverse_design.layout.feasibility import (  # noqa: E402
    PARAMETERIZED_GEOMETRY_NAMES,
    audit_parameterized_transformer_geometry,
)


SCHEMA = "historical_200k_fixed10k_emx_readiness_audit_v1"
QUEUE_SCHEMA = "historical_200k_fixed10k_one_shot_emx_queue_v1"
EXPECTED_ROW_COUNT = 10_000
EXPECTED_PASS_COUNT = 7_926
EXPECTED_FAIL_COUNT = 2_074
EXPECTED_LEGACY_PASS_COUNT = 6_439
EXPECTED_LEGACY_FAIL_COUNT = 1_561
EXPECTED_EXTENSION_PASS_COUNT = 1_487
EXPECTED_EXTENSION_FAIL_COUNT = 513
EXPECTED_FAILURE_CATEGORY = "power_line_8port_port_ground_overlap"
EXPECTED_MODEL_SEED = 20_260_711
EXPECTED_TRAINING_COUNT = 200_000
TARGET_COLUMNS = (
    "target__lp_nh_center",
    "target__ls_nh_center",
    "target__q_center",
    "target__k_abs_center",
)
PROXY_COLUMNS = (
    "proxy__lp_nh_center",
    "proxy__ls_nh_center",
    "proxy__q_center",
    "proxy__k_abs_center",
)
PRED_COLUMNS = (
    "pred_lp_nh_center",
    "pred_ls_nh_center",
    "pred_q_center",
    "pred_k_abs_center",
)
TARGET_KEYS = ("Lp_nH", "Ls_nH", "Q_min", "K_abs")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions-csv", required=True)
    parser.add_argument("--proxy-summary", required=True)
    parser.add_argument("--targets-json", required=True)
    parser.add_argument("--model-summary", required=True)
    parser.add_argument("--weights-npz", required=True)
    parser.add_argument("--trainer-source", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--out-dir", required=True)
    for source in (
        "predictions",
        "proxy-summary",
        "targets",
        "model-summary",
        "weights",
        "trainer",
        "config",
    ):
        parser.add_argument(f"--expected-{source}-sha256", required=True)
    args = parser.parse_args(argv)
    for name, value in vars(args).items():
        if name.startswith("expected_") and name.endswith("_sha256"):
            digest = str(value).strip().lower()
            if not _is_sha256(digest):
                parser.error(f"--{name.replace('_', '-')} must be a SHA-256")
            setattr(args, name, digest)
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    paths = {
        "predictions": _path(args.predictions_csv),
        "proxy_summary": _path(args.proxy_summary),
        "targets": _path(args.targets_json),
        "model_summary": _path(args.model_summary),
        "weights": _path(args.weights_npz),
        "trainer": _path(args.trainer_source),
        "config": _path(args.config),
    }
    expected_hashes = {
        "predictions": args.expected_predictions_sha256,
        "proxy_summary": args.expected_proxy_summary_sha256,
        "targets": args.expected_targets_sha256,
        "model_summary": args.expected_model_summary_sha256,
        "weights": args.expected_weights_sha256,
        "trainer": args.expected_trainer_sha256,
        "config": args.expected_config_sha256,
    }
    sources = {name: _file_record(path) for name, path in paths.items()}
    for name, expected in expected_hashes.items():
        if sources[name]["sha256"] != expected:
            raise ValueError(
                f"{name} SHA-256 mismatch: {sources[name]['sha256']} != {expected}"
            )

    out_dir = _path(args.out_dir)
    if out_dir.exists():
        raise FileExistsError(f"refusing existing output path: {out_dir}")

    prediction_rows, prediction_fields = _read_csv(paths["predictions"])
    proxy_summary = _read_json(paths["proxy_summary"])
    target_payload = _read_json(paths["targets"])
    model_summary = _read_json(paths["model_summary"])
    target_rows = target_payload.get("targets") or []
    if not isinstance(target_rows, list):
        raise ValueError("targets JSON does not contain a target list")
    target_by_id = {
        str(row.get("target_id") or ""): row
        for row in target_rows
        if isinstance(row, dict)
    }

    run_config = load_run_config(paths["config"])
    adapter = TransformerOptimizationAdapter(run_config.bounds)
    geometry_lower, geometry_upper = _geometry_bounds(run_config)
    summary_args = model_summary.get("arguments") or {}
    config_contract = _config_contract(run_config, geometry_lower, geometry_upper)
    source_contract_checks = {
        "prediction_row_count_exact_10000": len(prediction_rows)
        == EXPECTED_ROW_COUNT,
        "target_row_count_exact_10000": len(target_rows)
        == EXPECTED_ROW_COUNT,
        "prediction_columns_complete": {
            "row_index",
            "target_id",
            "panel",
            "inside_historical_training_contract",
            "model_seed",
            "inference_mode",
            "selected_geometry_sha256",
            *TARGET_COLUMNS,
            *PROXY_COLUMNS,
            *{f"geom__{name}" for name in PARAMETERIZED_GEOMETRY_NAMES},
        }.issubset(prediction_fields),
        "target_ids_unique": len(target_by_id) == EXPECTED_ROW_COUNT,
        "proxy_summary_status_exact": proxy_summary.get("overall_status")
        == "PASS_HISTORICAL_DIAGNOSTIC_NOT_CURRENT_MODEL_ACCEPTANCE",
        "proxy_summary_prediction_hash_exact": str(
            ((proxy_summary.get("outputs") or {}).get("predictions_csv_sha256"))
            or ""
        ).lower()
        == sources["predictions"]["sha256"],
        "proxy_summary_source_hashes_exact": _proxy_sources_match(
            proxy_summary, sources
        ),
        "model_training_count_exact_200000": int(
            model_summary.get("training_count") or 0
        )
        == EXPECTED_TRAINING_COUNT,
        "model_seed_exact_20260711": int(summary_args.get("seed") or -1)
        == EXPECTED_MODEL_SEED,
        "model_historical_k_support_exact": str(
            summary_args.get("physical_cell_lower") or ""
        )
        == "0.5,0.5,5,0"
        and str(summary_args.get("physical_cell_upper") or "")
        == "3,3,25,0.8",
        "model_scalar_q_input_column_exact": tuple(
            model_summary.get("input_columns") or ()
        )
        == (
            "input__lp_nh_center",
            "input__ls_nh_center",
            "input__q_center",
            "input__k_abs_center",
        ),
        "model_not_formally_accepted": model_summary.get(
            "eligible_for_checkpoint_model_acceptance"
        )
        is False,
        "target_schema_exact": target_payload.get("schema")
        == "direct_mlp_one_shot_targets_v1",
        "target_role_exact": target_payload.get("target_role")
        == "nonadvisor_fixed_proxy_frame",
        "target_q_semantics_minimum": target_payload.get("q_target_semantics")
        == "minimum",
        "target_frequency_exact_15ghz": math.isclose(
            float(target_payload.get("target_frequency_ghz") or 0.0),
            15.0,
            rel_tol=0.0,
            abs_tol=0.0,
        ),
        **config_contract["checks"],
    }
    _require_all(source_contract_checks, "source/config contract")

    readiness_rows: list[dict[str, Any]] = []
    valid_queue_rows: list[dict[str, Any]] = []
    pass_identity_rows: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    panel_status_counts: Counter[tuple[str, str]] = Counter()
    failure_categories: Counter[str] = Counter()
    failure_labels: Counter[str] = Counter()
    geometry_digests: set[str] = set()
    candidate_hashes: set[str] = set()
    geometry_hashes: set[str] = set()
    minimum_line_width = float("inf")
    maximum_line_width = float("-inf")

    for ordinal, source in enumerate(prediction_rows):
        row_index = _integer(source.get("row_index"), "row_index")
        target_id = str(source.get("target_id") or "")
        target = target_by_id.get(target_id)
        if target is None:
            raise ValueError(f"prediction target is absent from target frame: {target_id}")
        _check_target_row(source, target, ordinal)
        if row_index != ordinal:
            raise ValueError(f"prediction row order mismatch at ordinal {ordinal}")
        if str(source.get("inference_mode") or "") != "one_shot":
            raise ValueError(f"row {ordinal} is not one-shot")
        if _integer(source.get("model_seed"), "model_seed") != EXPECTED_MODEL_SEED:
            raise ValueError(f"row {ordinal} model seed mismatch")

        values = {
            name: _finite(source.get(f"geom__{name}"), f"geom__{name}")
            for name in PARAMETERIZED_GEOMETRY_NAMES
        }
        audit = audit_parameterized_transformer_geometry(
            values,
            run_config,
            adapter=adapter,
        )
        status = str(audit.get("status") or "FAIL")
        panel = str(source.get("panel") or "")
        categories = [str(value) for value in audit.get("failure_categories") or []]
        port_audit = audit.get("port_ground_overlap_audit") or {}
        labels = [str(value) for value in port_audit.get("failure_labels") or []]
        status_counts[status] += 1
        panel_status_counts[(panel, status)] += 1
        failure_categories.update(categories)
        failure_labels.update(labels)
        minimum_line_width = min(minimum_line_width, values["line_width_um"])
        maximum_line_width = max(maximum_line_width, values["line_width_um"])

        source_geometry_digest = str(
            source.get("selected_geometry_sha256") or ""
        ).lower()
        geometry_digest = _portable_geometry_digest(values)
        if source_geometry_digest != geometry_digest:
            raise ValueError(
                f"row {ordinal} portable 12-decimal geometry digest mismatch"
            )
        if not _is_sha256(geometry_digest) or geometry_digest in geometry_digests:
            raise ValueError(f"row {ordinal} geometry digest missing or duplicate")
        geometry_digests.add(geometry_digest)
        geometry_identity = identity._geometry_identity(values)
        target_record_sha = _json_sha256(target)
        pass_rank = len(valid_queue_rows) + 1 if status == "PASS" else None
        one_shot_identity = _json_sha256(
            {
                "schema": "historical_200k_target_plus_12decimal_geometry_identity_v1",
                "target_id": target_id,
                "portable_geometry_digest_sha256": geometry_digest,
                "model_seed": EXPECTED_MODEL_SEED,
                "model_weights_sha256": sources["weights"]["sha256"],
            }
        )
        candidate_id = (
            f"historical200k_seed20260711__{target_id}__g{one_shot_identity[:16]}"
        )
        candidate_hash = (
            identity._candidate_identity(
                candidate_id=candidate_id,
                geometry_identity=geometry_identity,
                seed=EXPECTED_MODEL_SEED,
                rank=int(pass_rank),
            )
            if pass_rank is not None
            else ""
        )

        readiness = {
            "row_index": row_index,
            "target_id": target_id,
            "panel": panel,
            "inside_historical_training_contract": _truthy(
                source.get("inside_historical_training_contract")
            ),
            "model_seed": EXPECTED_MODEL_SEED,
            "inference_mode": "one_shot",
            "geometry_projection_or_repair_used": False,
            "analytical_preflight_status": status,
            "eligible_for_fresh_emx": status == "PASS",
            "failure_categories": "|".join(categories),
            "port_ground_failure_labels": "|".join(labels),
            "port_ground_expected_overlap_um": port_audit.get(
                "expected_overlap_um", ""
            ),
            "target_record_sha256": target_record_sha,
            "source_geometry_digest_sha256": geometry_digest,
            "frozen_target_plus_geometry_identity_sha256": one_shot_identity,
            "candidate_id": candidate_id if status == "PASS" else "",
            "candidate_id_sha256": candidate_hash,
            "candidate_geometry_identity_sha256": geometry_identity,
        }
        for name in TARGET_COLUMNS + PROXY_COLUMNS:
            readiness[name] = _finite(source.get(name), name)
        readiness.update({f"geom__{name}": values[name] for name in values})
        readiness_rows.append(readiness)

        if status != "PASS":
            continue
        if candidate_hash in candidate_hashes:
            raise ValueError(f"duplicate candidate identity: {candidate_hash}")
        if geometry_identity in geometry_hashes:
            raise ValueError(f"duplicate geometry identity: {geometry_identity}")
        candidate_hashes.add(candidate_hash)
        geometry_hashes.add(geometry_identity)
        queue = _queue_row(
            source=source,
            target_record_sha=target_record_sha,
            values=values,
            pass_rank=int(pass_rank),
            row_index=row_index,
            candidate_id=candidate_id,
            candidate_hash=candidate_hash,
            geometry_identity=geometry_identity,
            one_shot_identity=one_shot_identity,
            source_hashes=sources,
        )
        valid_queue_rows.append(queue)
        pass_identity_rows.append(
            {
                "pass_rank": int(pass_rank),
                "source_row_index": row_index,
                "target_id": target_id,
                "panel": panel,
                "candidate_id": candidate_id,
                "candidate_id_sha256": candidate_hash,
                "candidate_geometry_identity_sha256": geometry_identity,
                "source_geometry_digest_sha256": geometry_digest,
                "frozen_target_plus_geometry_identity_sha256": one_shot_identity,
            }
        )

    result_checks = {
        "all_10000_rows_audited": len(readiness_rows) == EXPECTED_ROW_COUNT,
        "passing_queue_count_exact_7926": len(valid_queue_rows)
        == EXPECTED_PASS_COUNT,
        "failure_count_exact_2074": status_counts["FAIL"]
        == EXPECTED_FAIL_COUNT,
        "legacy_pass_count_exact_6439": panel_status_counts[
            ("legacy_k_le_0p8", "PASS")
        ]
        == EXPECTED_LEGACY_PASS_COUNT,
        "legacy_fail_count_exact_1561": panel_status_counts[
            ("legacy_k_le_0p8", "FAIL")
        ]
        == EXPECTED_LEGACY_FAIL_COUNT,
        "extension_pass_count_exact_1487": panel_status_counts[
            ("extension_k_gt_0p8", "PASS")
        ]
        == EXPECTED_EXTENSION_PASS_COUNT,
        "extension_fail_count_exact_513": panel_status_counts[
            ("extension_k_gt_0p8", "FAIL")
        ]
        == EXPECTED_EXTENSION_FAIL_COUNT,
        "only_expected_failure_category": dict(failure_categories)
        == {EXPECTED_FAILURE_CATEGORY: EXPECTED_FAIL_COUNT},
        "all_geometry_digests_unique": len(geometry_digests)
        == EXPECTED_ROW_COUNT,
        "all_pass_candidate_hashes_unique": len(candidate_hashes)
        == EXPECTED_PASS_COUNT,
        "all_pass_geometry_hashes_unique": len(geometry_hashes)
        == EXPECTED_PASS_COUNT,
        "minimum_line_width_respects_historical_3um_bound": minimum_line_width
        >= 3.0,
        "maximum_line_width_respects_historical_12um_bound": maximum_line_width
        <= 12.0,
        "no_geometry_projection_or_repair": True,
        "literal_10000_fresh_emx_is_not_authorized": len(valid_queue_rows)
        < EXPECTED_ROW_COUNT,
    }
    _require_all(result_checks, "frozen readiness result")

    out_dir.mkdir(parents=True, exist_ok=False)
    readiness_csv = out_dir / "historical_200k_fixed10k_emx_readiness_rows.csv"
    queue_csv = out_dir / "historical_200k_fixed10k_valid_one_shot_emx_queue.csv"
    identities_csv = out_dir / "historical_200k_fixed10k_pass_row_identities.csv"
    _write_csv(readiness_csv, readiness_rows)
    _write_csv(queue_csv, valid_queue_rows)
    _write_csv(identities_csv, pass_identity_rows)

    runtime_sources = _runtime_source_records()
    artifacts = {
        "all_readiness_rows": _file_record(readiness_csv, len(readiness_rows)),
        "valid_one_shot_emx_queue": _file_record(queue_csv, len(valid_queue_rows)),
        "pass_row_identities": _file_record(identities_csv, len(pass_identity_rows)),
    }
    summary = {
        "schema": SCHEMA,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": "PASS",
        "decision": "GO_UNCHANGED_7926_ONLY_NO_GO_FAILED_2074",
        "sources": sources,
        "runtime_sources": runtime_sources,
        "source_contract_checks": source_contract_checks,
        "result_checks": result_checks,
        "config_contract": config_contract,
        "model_contract": {
            "training_count": EXPECTED_TRAINING_COUNT,
            "seed": EXPECTED_MODEL_SEED,
            "historical_k_abs_support": [0.0, 0.8],
            "line_width_um_support": [3.0, 12.0],
            "q_target_semantics": "minimum",
            "inference_mode": "one_shot",
            "geometry_projection_or_repair_used": False,
            "formally_accepted_current_model": False,
        },
        "counts": {
            "frozen_target_count": EXPECTED_ROW_COUNT,
            "analytical_preflight_pass": status_counts["PASS"],
            "analytical_preflight_fail": status_counts["FAIL"],
            "analytical_preflight_pass_fraction": status_counts["PASS"]
            / EXPECTED_ROW_COUNT,
            "legacy_k_le_0p8": {
                "pass": panel_status_counts[("legacy_k_le_0p8", "PASS")],
                "fail": panel_status_counts[("legacy_k_le_0p8", "FAIL")],
            },
            "extension_k_gt_0p8": {
                "pass": panel_status_counts[("extension_k_gt_0p8", "PASS")],
                "fail": panel_status_counts[("extension_k_gt_0p8", "FAIL")],
            },
        },
        "failure_categories": dict(sorted(failure_categories.items())),
        "port_ground_failure_labels": dict(sorted(failure_labels.items())),
        "observed_line_width_um": {
            "minimum": minimum_line_width,
            "maximum": maximum_line_width,
        },
        "passing_candidate_identity_aggregate_sha256": _json_sha256(
            pass_identity_rows
        ),
        "artifacts": artifacts,
        "execution_authorization": {
            "fresh_emx_allowed_only_for_unchanged_pass_queue": True,
            "allowed_candidate_count": EXPECTED_PASS_COUNT,
            "failed_candidate_count": EXPECTED_FAIL_COUNT,
            "repair_or_projection_authorized": False,
            "bypass_port_ground_gate_authorized": False,
            "literal_full_10000_emx_authorized": False,
            "foundry_calibre_required_before_fresh_emx": True,
            "automatic_pool_merge_authorized": False,
            "automatic_model_promotion_authorized": False,
        },
        "scientific_boundary": (
            "PASS proves that all 10000 frozen one-shot outputs were replayed "
            "through the exact analytical geometry and port-ground gate under "
            "the historical 3.0 um line-width configuration. The 2074 failed "
            "rows remain failures in the denominator and must not be repaired "
            "or simulated as if they were unchanged one-shot outputs. The 7926 "
            "passing rows are only eligible for candidate-bound Cadence GDS, "
            "Calibre macro/IP back-end DRC, and fresh real EMX; this audit is "
            "not an EMX accuracy result. The K>0.8 rows remain explicitly "
            "out-of-training-contract extrapolation evidence."
        ),
    }
    summary_path = out_dir / "historical_200k_fixed10k_emx_readiness_summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print("overall_status=PASS")
    print("decision=GO_UNCHANGED_7926_ONLY_NO_GO_FAILED_2074")
    print(f"valid_queue_count={len(valid_queue_rows)}")
    print(f"failed_count={status_counts['FAIL']}")
    print(f"queue_csv={queue_csv}")
    print(f"summary={summary_path}")
    return 0


def _queue_row(
    *,
    source: dict[str, str],
    target_record_sha: str,
    values: dict[str, float],
    pass_rank: int,
    row_index: int,
    candidate_id: str,
    candidate_hash: str,
    geometry_identity: str,
    one_shot_identity: str,
    source_hashes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "queue_schema": QUEUE_SCHEMA,
        "selection_rank": pass_rank,
        "candidate_index": row_index,
        "candidate_id": candidate_id,
        "candidate_id_sha256": candidate_hash,
        "candidate_geometry_identity_sha256": geometry_identity,
        "candidate_identity_schema": identity.CANDIDATE_IDENTITY_SCHEMA,
        "geometry_identity_schema": identity.IDENTITY_SCHEMA,
        "queue_seed": EXPECTED_MODEL_SEED,
        "target_id": str(source["target_id"]),
        "target_record_sha256": target_record_sha,
        "target_frequency_ghz": 15.0,
        "panel": str(source["panel"]),
        "inside_historical_training_contract": _truthy(
            source.get("inside_historical_training_contract")
        ),
        "selection_source": "frozen_fixed10k_order_after_analytical_safety_gate",
        "candidate_generation_mode": "historical_200k_frozen_inverse_mlp_one_shot",
        "prediction_value_source": "frozen_forward_proxy_diagnostic_only_not_physical_label",
        "model_seed": EXPECTED_MODEL_SEED,
        "label_status": "UNLABELED_AWAITING_FRESH_REAL_EMX",
        "local_preflight_status": "PASS",
        "drc_status": "ANALYTICAL_PASS_CALIBRE_REQUIRED",
        "projection_applied": "false",
        "geometry_projection_or_repair_used": "false",
        "local_proxy_refinement_used": "false",
        "target_local_emx_feedback_used": "false",
        "candidate_search_or_ranking_used": "false",
        "automatic_physical_delivery_authorized": "false",
        "automatic_production_acceptance_authorized": "false",
        "source_row_index": row_index,
        "source_geometry_digest_sha256": str(
            source["selected_geometry_sha256"]
        ).lower(),
        "frozen_target_plus_geometry_identity_sha256": one_shot_identity,
        "source_predictions_csv_sha256": source_hashes["predictions"]["sha256"],
        "primary_model_id": "historical_200k_tandem_seed20260711_unaccepted",
        "primary_model_seed": EXPECTED_MODEL_SEED,
        "primary_model_summary_sha256": source_hashes["model_summary"]["sha256"],
        "primary_model_weights_sha256": source_hashes["weights"]["sha256"],
        "trainer_source_sha256": source_hashes["trainer"]["sha256"],
        "target_frame_sha256": source_hashes["targets"]["sha256"],
        "runtime_config_sha256": source_hashes["config"]["sha256"],
    }
    for target_name in TARGET_COLUMNS:
        row[target_name] = _finite(source.get(target_name), target_name)
    for source_name, output_name in zip(PROXY_COLUMNS, PRED_COLUMNS):
        row[output_name] = _finite(source.get(source_name), source_name)
    row.update({f"geom__{name}": values[name] for name in values})
    row["geom__primary_width_um"] = values["line_width_um"]
    row["geom__secondary_width_um"] = values["line_width_um"]
    return row


def _config_contract(
    run_config: Any,
    lower: dict[str, float],
    upper: dict[str, float],
) -> dict[str, Any]:
    frequencies = list(run_config.target.frequency_points_hz())
    power_line = run_config.emx.power_line_8port
    checks = {
        "config_line_width_lower_exact_3um": math.isclose(
            lower["line_width_um"], 3.0, rel_tol=0.0, abs_tol=0.0
        ),
        "config_line_width_upper_exact_12um": math.isclose(
            upper["line_width_um"], 12.0, rel_tol=0.0, abs_tol=0.0
        ),
        "config_port_mode_exact": run_config.emx.port_mode
        == "single_ended_shield_grounded",
        "config_pin_purpose_exact_51": int(run_config.emx.cadence_pin_purpose)
        == 51,
        "config_touchstone_mode_exact_s4p": power_line.touchstone_mode
        == "signal_4_grounded_aux",
        "config_port_map_exact_four": tuple(power_line.port_map)
        == ("P001", "P002", "P003", "P004"),
        "config_frequency_start_exact_5ghz": math.isclose(
            float(frequencies[0]), 5.0e9, rel_tol=0.0, abs_tol=1.0
        ),
        "config_frequency_stop_exact_60ghz": math.isclose(
            float(frequencies[-1]), 60.0e9, rel_tol=0.0, abs_tol=1.0
        ),
        "config_frequency_points_exact_111": len(frequencies) == 111,
        "config_frequency_step_exact_0p5ghz": all(
            math.isclose(
                float(right - left), 0.5e9, rel_tol=0.0, abs_tol=1.0
            )
            for left, right in zip(frequencies[:-1], frequencies[1:])
        ),
    }
    return {
        "checks": checks,
        "geometry_lower": lower,
        "geometry_upper": upper,
        "port_mode": run_config.emx.port_mode,
        "cadence_pin_purpose": int(run_config.emx.cadence_pin_purpose),
        "touchstone_mode": power_line.touchstone_mode,
        "port_map": list(power_line.port_map),
        "frequency_start_hz": float(frequencies[0]),
        "frequency_stop_hz": float(frequencies[-1]),
        "frequency_step_hz": float(frequencies[1] - frequencies[0]),
        "frequency_points": len(frequencies),
    }


def _geometry_bounds(run_config: Any) -> tuple[dict[str, float], dict[str, float]]:
    adapter = TransformerOptimizationAdapter(run_config.bounds)
    bounds = list(run_config.bounds.to_scipy_bounds())
    names = list(adapter.field_order())
    raw = {name: pair for name, pair in zip(names, bounds)}
    lower: dict[str, float] = {}
    upper: dict[str, float] = {}
    for name in PARAMETERIZED_GEOMETRY_NAMES:
        lookup = "primary_width_um" if name == "line_width_um" else name
        pair = raw[lookup]
        lower[name] = float(pair[0])
        upper[name] = float(pair[1])
    return lower, upper


def _proxy_sources_match(
    proxy_summary: dict[str, Any],
    sources: dict[str, dict[str, Any]],
) -> bool:
    recorded = proxy_summary.get("sources") or {}
    mapping = {
        "weights": "weights",
        "model_summary": "model_summary",
        "targets": "targets",
        "trainer_source": "trainer",
    }
    return all(
        str((recorded.get(proxy_name) or {}).get("sha256") or "").lower()
        == sources[source_name]["sha256"]
        for proxy_name, source_name in mapping.items()
    )


def _check_target_row(
    prediction: dict[str, str],
    target: dict[str, Any],
    ordinal: int,
) -> None:
    for prediction_name, target_name in zip(TARGET_COLUMNS, TARGET_KEYS):
        left = _finite(prediction.get(prediction_name), prediction_name)
        right = _finite(target.get(target_name), target_name)
        if not math.isclose(left, right, rel_tol=0.0, abs_tol=0.0):
            raise ValueError(
                f"target value mismatch at row {ordinal}: {prediction_name}"
            )


def _runtime_source_records() -> dict[str, dict[str, Any]]:
    feasibility_source = Path(
        inspect.getsourcefile(audit_parameterized_transformer_geometry) or ""
    ).resolve()
    identity_source = Path(inspect.getsourcefile(identity) or "").resolve()
    return {
        "audit_script": _file_record(Path(__file__).resolve()),
        "geometry_feasibility_module": _file_record(feasibility_source),
        "candidate_identity_module": _file_record(identity_source),
    }


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _read_csv(path: Path) -> tuple[list[dict[str, str]], set[str]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader], set(reader.fieldnames or [])


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing empty CSV: {path}")
    fields: list[str] = []
    for row in rows:
        for name in row:
            if name not in fields:
                fields.append(name)
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _file_record(path: Path, row_count: int | None = None) -> dict[str, Any]:
    record = {
        "path": str(path),
        "exists": path.is_file(),
        "size_bytes": path.stat().st_size if path.is_file() else None,
        "sha256": _sha256(path) if path.is_file() else "",
    }
    if row_count is not None:
        record["row_count"] = int(row_count)
    return record


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _portable_geometry_digest(values: dict[str, float]) -> str:
    """Match the frozen evaluator's cross-platform 12-decimal digest."""
    vector = np.asarray(
        [values[name] for name in PARAMETERIZED_GEOMETRY_NAMES],
        dtype=np.float64,
    )
    rounded = np.round(vector, decimals=12)
    return hashlib.sha256(rounded.tobytes()).hexdigest()


def _finite(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} is not numeric: {value!r}") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} is not finite: {value!r}")
    return result


def _integer(value: Any, name: str) -> int:
    number = _finite(value, name)
    if not number.is_integer():
        raise ValueError(f"{name} is not an integer: {value!r}")
    return int(number)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "pass"}


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdef" for character in value.lower()
    )


def _require_all(checks: dict[str, bool], label: str) -> None:
    failed = [name for name, passed in checks.items() if passed is not True]
    if failed:
        raise ValueError(f"{label} failed: {failed}")


if __name__ == "__main__":
    raise SystemExit(main())
