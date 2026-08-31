#!/usr/bin/env python3
"""Select one contract-bound 5k Phase-B/C candidate queue.

The selector consumes the staged adaptive-round contract and its hash-bound
real-EMX checkpoint evidence.  Ensemble predictions may affect candidate
priority only.  The output remains unlabeled until fresh Cadence, Calibre, and
EMX evidence passes the next round audit.
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
from scipy.spatial import cKDTree

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rfic_transformer_inverse_design.campaigns.broadband56_adaptive_selection import (  # noqa: E402
    MINIMUM_CANDIDATE_POOL_FACTOR,
    PREDICTION_FEATURES,
    compute_candidate_components,
    prediction_column,
    required_prediction_columns,
    select_source_quotas,
    selection_policy_contract,
    source_static_scores,
    uncertainty_column,
)
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    ADAPTIVE_BATCH_SIZE,
    ANCHOR_FREQUENCIES_GHZ,
    CAMPAIGN_ID,
    GEOMETRY_FIELDS,
    PRIMARY_CELLS_PER_ANCHOR,
    canonical_geometry_sha256,
    contract_fingerprint,
    validate_contract,
)
from rfic_transformer_inverse_design.campaigns.broadband56_geometry_coverage import (  # noqa: E402
    validate_geometry_bounds_payload,
)


ACCEPTANCE_STATUS_FIELDS = (
    "analytical_status",
    "topology_status",
    "cadence_gds_status",
    "calibre_status",
    "emx_status",
    "s4p_status",
    "feature_extraction_status",
)
CANDIDATE_GATE_FIELDS = (
    "analytical_status",
    "topology_status",
    "top_metal_drc_status",
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    contract_path = Path(args.contract).expanduser().resolve()
    round_dir = Path(args.round_dir).expanduser().resolve()
    candidate_path = Path(args.candidate_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        print(f"refusing to overwrite existing output directory: {out_dir}", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True)

    checks: list[dict[str, Any]] = []
    contract = _read_json(contract_path, checks, "campaign_contract")
    errors = validate_contract(contract) if contract else ["contract unavailable"]
    checks.append(_check("campaign_contract_valid", not errors, errors))
    fingerprint = str(contract.get("contract_fingerprint_sha256") or contract_fingerprint(contract)) if contract else ""

    round_contract_path = round_dir / "ADAPTIVE_ROUND_CONTRACT.json"
    round_receipt_path = round_dir / "ADAPTIVE_ROUND_RECEIPT.json"
    round_contract = _read_json(round_contract_path, checks, "adaptive_round_contract")
    round_receipt = _read_json(round_receipt_path, checks, "adaptive_round_receipt")
    round_info = round_contract.get("round") if isinstance(round_contract.get("round"), dict) else {}
    source_quotas = round_contract.get("active_source_quotas") if isinstance(round_contract.get("active_source_quotas"), dict) else {}
    accepted_start = _integer(round_info.get("accepted_start"))
    accepted_target = _integer(round_info.get("accepted_target"))
    phase = str(round_info.get("phase") or "")
    acquisition_mode = str(round_contract.get("acquisition_mode") or "")
    checks.extend(
        [
            _check(
                "adaptive_round_contract_pass",
                round_contract.get("overall_status") == "PASS"
                and round_contract.get("campaign_id") == CAMPAIGN_ID
                and round_contract.get("campaign_contract_fingerprint") == fingerprint,
                {
                    "status": round_contract.get("overall_status"),
                    "campaign_id": round_contract.get("campaign_id"),
                    "fingerprint": round_contract.get("campaign_contract_fingerprint"),
                },
            ),
            _check(
                "adaptive_round_receipt_pass",
                round_receipt.get("overall_status") == "PASS"
                and round_receipt.get("campaign_id") == CAMPAIGN_ID
                and round_receipt.get("campaign_contract_fingerprint") == fingerprint,
                round_receipt.get("decision"),
            ),
            _check(
                "adaptive_round_contract_hash_bound",
                _output_evidence_matches(round_receipt, "adaptive_round_contract", round_contract_path),
                str(round_contract_path),
            ),
            _check(
                "candidate_selection_policy_exact",
                round_contract.get("candidate_selection_policy") == selection_policy_contract(),
                round_contract.get("candidate_selection_policy"),
            ),
            _check(
                "round_size_and_source_quotas_exact",
                accepted_target - accepted_start == ADAPTIVE_BATCH_SIZE
                and sum(_integer(value) for value in source_quotas.values()) == ADAPTIVE_BATCH_SIZE,
                {"accepted_start": accepted_start, "accepted_target": accepted_target, "source_quotas": source_quotas},
            ),
            _check(
                "acquisition_mode_supported",
                acquisition_mode in {"ENSEMBLE_ACQUISITION", "FALLBACK_MAXIMIN"},
                acquisition_mode,
            ),
        ]
    )

    preceding = round_contract.get("preceding_real_emx_audit") if isinstance(round_contract.get("preceding_real_emx_audit"), dict) else {}
    accepted_path = Path(str(preceding.get("accepted_geometries_path") or "")).expanduser().resolve()
    bounds_path = Path(str(preceding.get("geometry_bounds_path") or "")).expanduser().resolve()
    coverage_path = Path(str(preceding.get("coverage_cells_path") or "")).expanduser().resolve()
    checks.extend(
        [
            _check(
                "accepted_geometry_evidence_hash_bound",
                _evidence_matches(accepted_path, str(preceding.get("accepted_geometries_sha256") or "")),
                str(accepted_path),
            ),
            _check(
                "geometry_bounds_evidence_hash_bound",
                _evidence_matches(bounds_path, str(preceding.get("geometry_bounds_sha256") or "")),
                str(bounds_path),
            ),
            _check(
                "coverage_cells_evidence_hash_bound",
                _evidence_matches(coverage_path, str(preceding.get("coverage_cells_sha256") or "")),
                str(coverage_path),
            ),
        ]
    )

    bounds_payload = _read_json(bounds_path, checks, "geometry_bounds")
    bounds_errors = validate_geometry_bounds_payload(bounds_payload, contract_fingerprint_sha256=fingerprint) if bounds_payload else ["bounds unavailable"]
    checks.append(_check("geometry_bounds_contract_valid", not bounds_errors, bounds_errors))
    bounds = bounds_payload.get("field_bounds_um") if isinstance(bounds_payload.get("field_bounds_um"), dict) else {}
    accepted = _load_accepted(accepted_path, bounds, fingerprint, accepted_start)
    checks.extend(accepted.pop("checks"))
    coverage = _load_coverage_cells(coverage_path)
    checks.extend(coverage.pop("checks"))

    ensemble_sha = ""
    ensemble_gate = round_contract.get("ensemble_gate") if isinstance(round_contract.get("ensemble_gate"), dict) else {}
    ensemble_evidence = ensemble_gate.get("receipt") if isinstance(ensemble_gate.get("receipt"), dict) else {}
    if acquisition_mode == "ENSEMBLE_ACQUISITION":
        ensemble_sha = str(ensemble_evidence.get("sha256") or "")
        checks.append(_check("ensemble_receipt_sha_available", _is_sha256(ensemble_sha), ensemble_sha))
    candidates = _load_candidates(
        candidate_path,
        bounds=bounds,
        fingerprint=fingerprint,
        accepted_hashes=accepted.get("hash_set") or set(),
        require_predictions=acquisition_mode == "ENSEMBLE_ACQUISITION",
        ensemble_sha=ensemble_sha,
    )
    checks.extend(candidates.pop("checks"))
    checks.append(
        _check(
            "candidate_pool_is_large",
            int(candidates.get("count") or 0) >= MINIMUM_CANDIDATE_POOL_FACTOR * ADAPTIVE_BATCH_SIZE,
            {
                "actual": candidates.get("count"),
                "minimum": MINIMUM_CANDIDATE_POOL_FACTOR * ADAPTIVE_BATCH_SIZE,
            },
        )
    )

    analysis: dict[str, Any] = {}
    selected_rows: list[dict[str, Any]] = []
    if checks and all(item["pass"] for item in checks):
        try:
            analysis = _select(
                acquisition_mode=acquisition_mode,
                source_quotas={str(key): _integer(value) for key, value in source_quotas.items()},
                accepted=accepted,
                candidates=candidates,
                coverage=coverage,
            )
            selected_rows = _materialize_selected_rows(
                candidates["rows"],
                analysis,
                phase=phase,
                accepted_start=accepted_start,
                accepted_target=accepted_target,
            )
            checks.extend(_selection_checks(analysis, selected_rows, source_quotas, accepted.get("hash_set") or set()))
        except (KeyError, TypeError, ValueError) as exc:
            checks.append(_check("candidate_selection_completed", False, f"{type(exc).__name__}: {exc}"))

    status = "PASS" if checks and all(item["pass"] for item in checks) else "FAIL"
    queue_path = out_dir / "broadband56_adaptive_candidate_queue.csv"
    if status == "PASS":
        _write_csv(queue_path, selected_rows)

    summary_path = out_dir / "ADAPTIVE_SELECTION_SUMMARY.json"
    summary = {
        "schema": "broadband56_adaptive_selection_summary_v1",
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": "USE_AS_UNLABELED_REAL_EMX_CANDIDATE_QUEUE" if status == "PASS" else "DO_NOT_RUN_CADENCE_CALIBRE_OR_EMX",
        "campaign_id": CAMPAIGN_ID,
        "campaign_contract_fingerprint": fingerprint,
        "phase": phase,
        "accepted_start": accepted_start,
        "accepted_target": accepted_target,
        "acquisition_mode": acquisition_mode,
        "active_source_quotas": source_quotas,
        "candidate_pool_count": candidates.get("count", 0),
        "selected_count": len(selected_rows),
        "selected_counts_by_source": analysis.get("selected_counts_by_source") if analysis else {},
        "selected_geometry_sha256_digest": _string_digest([row["geometry_sha256"] for row in selected_rows]) if selected_rows else None,
        "selection_policy": selection_policy_contract(),
        "inputs": {
            "campaign_contract": _file_evidence(contract_path),
            "adaptive_round_contract": _file_evidence(round_contract_path),
            "adaptive_round_receipt": _file_evidence(round_receipt_path),
            "accepted_geometries": _file_evidence(accepted_path),
            "geometry_bounds": _file_evidence(bounds_path),
            "coverage_cells": _file_evidence(coverage_path),
            "candidate_pool": _file_evidence(candidate_path),
            "ensemble_receipt": ensemble_evidence if acquisition_mode == "ENSEMBLE_ACQUISITION" else None,
        },
        "checks": checks,
        "scientific_boundary": (
            "All predicted responses, cells, uncertainties, and selection scores are candidate-priority provenance only. "
            "The selected rows remain unlabeled and contribute zero accepted or coverage counts until fresh Cadence, "
            "zero-blocking Calibre, and real EMX evidence passes the next audit."
        ),
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    receipt_path = out_dir / "ADAPTIVE_SELECTION_RECEIPT.json"
    receipt = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": summary["decision"],
        "campaign_id": CAMPAIGN_ID,
        "campaign_contract_fingerprint": fingerprint,
        "checks": checks,
        "outputs": {
            "selection_summary": _file_evidence(summary_path),
            "candidate_queue": _file_evidence(queue_path) if queue_path.is_file() else None,
        },
    }
    receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _write_sha256s(out_dir)

    print(f"overall_status={status}")
    print(f"decision={summary['decision']}")
    print(f"selected_count={len(selected_rows)}")
    print(f"summary={summary_path}")
    if queue_path.is_file():
        print(f"candidate_queue={queue_path}")
    return 0 if status == "PASS" else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--round-dir", required=True)
    parser.add_argument("--candidate-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    return parser.parse_args(argv)


def _load_accepted(path: Path, bounds: dict[str, Any], fingerprint: str, expected: int) -> dict[str, Any]:
    rows, fields = _read_csv(path)
    required = {
        "geometry_sha256",
        "campaign_contract_fingerprint",
        "calibre_blocking_violations",
        *ACCEPTANCE_STATUS_FIELDS,
        *(f"geom__{name}" for name in GEOMETRY_FIELDS),
    }
    errors: list[str] = []
    matrix: list[list[float]] = []
    hashes: list[str] = []
    for row_index, row in enumerate(rows, start=2):
        try:
            values = [float(row[f"geom__{name}"]) for name in GEOMETRY_FIELDS]
        except (KeyError, TypeError, ValueError):
            errors.append(f"line {row_index}: invalid geometry values")
            continue
        geometry = {name: value for name, value in zip(GEOMETRY_FIELDS, values)}
        digest = str(row.get("geometry_sha256") or "").lower()
        if digest != canonical_geometry_sha256(geometry):
            errors.append(f"line {row_index}: canonical geometry hash mismatch")
        if row.get("campaign_contract_fingerprint") != fingerprint:
            errors.append(f"line {row_index}: campaign fingerprint mismatch")
        if any(str(row.get(field) or "").upper() != "PASS" for field in ACCEPTANCE_STATUS_FIELDS):
            errors.append(f"line {row_index}: accepted gate is not PASS")
        if _integer(row.get("calibre_blocking_violations")) != 0:
            errors.append(f"line {row_index}: blocking Calibre violations are nonzero")
        matrix.append(values)
        hashes.append(digest)
    normalized, bound_errors = _normalize_geometry(matrix, bounds)
    errors.extend(bound_errors)
    checks = [
        _check("accepted_columns_present", required.issubset(fields), sorted(required - fields)),
        _check("accepted_count_matches_round_start", len(rows) == expected, {"actual": len(rows), "expected": expected}),
        _check("accepted_rows_valid", not errors, errors[:20]),
        _check("accepted_geometry_hashes_unique", len(set(hashes)) == len(rows), {"unique": len(set(hashes)), "rows": len(rows)}),
    ]
    return {"checks": checks, "rows": rows, "normalized": normalized, "hashes": hashes, "hash_set": set(hashes)}


def _load_candidates(
    path: Path,
    *,
    bounds: dict[str, Any],
    fingerprint: str,
    accepted_hashes: set[str],
    require_predictions: bool,
    ensemble_sha: str,
) -> dict[str, Any]:
    rows, fields = _read_csv(path)
    required = {
        "candidate_id",
        "candidate_id_sha256",
        "candidate_geometry_identity_sha256",
        "candidate_identity_schema",
        "campaign_id",
        "campaign_contract_fingerprint",
        "geometry_sha256",
        "candidate_generation_mode",
        "candidate_generation_seed",
        *CANDIDATE_GATE_FIELDS,
        *(f"geom__{name}" for name in GEOMETRY_FIELDS),
    }
    if require_predictions:
        required.update(required_prediction_columns())
        required.add("ensemble_receipt_sha256")
    errors: list[str] = []
    matrix: list[list[float]] = []
    hashes: list[str] = []
    candidate_ids: list[str] = []
    for row_index, row in enumerate(rows, start=2):
        try:
            values = [float(row[f"geom__{name}"]) for name in GEOMETRY_FIELDS]
        except (KeyError, TypeError, ValueError):
            errors.append(f"line {row_index}: invalid geometry values")
            continue
        geometry = {name: value for name, value in zip(GEOMETRY_FIELDS, values)}
        digest = str(row.get("geometry_sha256") or "").lower()
        if digest != canonical_geometry_sha256(geometry):
            errors.append(f"line {row_index}: canonical geometry hash mismatch")
        if digest in accepted_hashes:
            errors.append(f"line {row_index}: candidate repeats accepted geometry")
        if row.get("campaign_id") != CAMPAIGN_ID or row.get("campaign_contract_fingerprint") != fingerprint:
            errors.append(f"line {row_index}: campaign identity mismatch")
        if any(str(row.get(field) or "").upper() != "PASS" for field in CANDIDATE_GATE_FIELDS):
            errors.append(f"line {row_index}: analytical/topology gate is not PASS")
        if not str(row.get("candidate_generation_mode") or "") or _integer(row.get("candidate_generation_seed")) < 0:
            errors.append(f"line {row_index}: candidate generation provenance is missing")
        candidate_id = str(row.get("candidate_id") or "").strip()
        if not candidate_id:
            errors.append(f"line {row_index}: candidate_id is missing")
        if str(row.get("candidate_id_sha256") or "").lower() != digest:
            errors.append(f"line {row_index}: candidate_id_sha256 mismatch")
        if (
            str(row.get("candidate_geometry_identity_sha256") or "").lower()
            != digest
        ):
            errors.append(
                f"line {row_index}: candidate_geometry_identity_sha256 mismatch"
            )
        if (
            str(row.get("candidate_identity_schema") or "")
            != "canonical_10d_geometry_sha256_alias_v1"
        ):
            errors.append(f"line {row_index}: candidate identity schema mismatch")
        if require_predictions and str(row.get("ensemble_receipt_sha256") or "").lower() != ensemble_sha:
            errors.append(f"line {row_index}: ensemble receipt SHA mismatch")
        matrix.append(values)
        hashes.append(digest)
        candidate_ids.append(candidate_id)
    normalized, bound_errors = _normalize_geometry(matrix, bounds)
    errors.extend(bound_errors)
    checks = [
        _check("candidate_pool_exists", path.is_file(), str(path)),
        _check("candidate_columns_present", required.issubset(fields), sorted(required - fields)),
        _check("candidate_pool_nonempty", bool(rows), len(rows)),
        _check("candidate_rows_valid", not errors, errors[:20]),
        _check(
            "candidate_ids_unique",
            len(set(candidate_ids)) == len(rows),
            {"unique": len(set(candidate_ids)), "rows": len(rows)},
        ),
        _check("candidate_geometry_hashes_unique", len(set(hashes)) == len(rows), {"unique": len(set(hashes)), "rows": len(rows)}),
    ]
    return {"checks": checks, "rows": rows, "count": len(rows), "normalized": normalized, "hashes": hashes}


def _load_coverage_cells(path: Path) -> dict[str, Any]:
    rows, fields = _read_csv(path)
    required = {
        "anchor_ghz",
        "local_cell_index",
        "conditioned_cell_index",
        "actual_count",
        "target_count",
        "deficit",
    }
    deficits = np.zeros((len(ANCHOR_FREQUENCIES_GHZ), PRIMARY_CELLS_PER_ANCHOR), dtype=float)
    targets = np.zeros_like(deficits)
    seen: set[tuple[int, int]] = set()
    errors: list[str] = []
    anchor_to_index = {anchor: index for index, anchor in enumerate(ANCHOR_FREQUENCIES_GHZ)}
    for row_index, row in enumerate(rows, start=2):
        anchor = _integer(row.get("anchor_ghz"))
        local = _integer(row.get("local_cell_index"))
        conditioned = _integer(row.get("conditioned_cell_index"))
        actual = _finite(row.get("actual_count"))
        target = _finite(row.get("target_count"))
        deficit = _finite(row.get("deficit"))
        if (
            anchor not in anchor_to_index
            or not 0 <= local < PRIMARY_CELLS_PER_ANCHOR
            or actual is None
            or target is None
            or deficit is None
        ):
            errors.append(f"line {row_index}: invalid coverage cell")
            continue
        anchor_index = anchor_to_index[anchor]
        expected_conditioned = anchor_index * PRIMARY_CELLS_PER_ANCHOR + local
        expected_deficit = max(target - actual, 0.0)
        if (
            actual < 0.0
            or not actual.is_integer()
            or target <= 0.0
            or deficit < 0.0
            or conditioned != expected_conditioned
            or not math.isclose(deficit, expected_deficit, rel_tol=0.0, abs_tol=1.0e-9)
        ):
            errors.append(f"line {row_index}: invalid target/deficit")
        key = (anchor, local)
        if key in seen:
            errors.append(f"line {row_index}: duplicate coverage cell")
        seen.add(key)
        deficits[anchor_index, local] = deficit
        targets[anchor_index, local] = target
    checks = [
        _check("coverage_columns_present", required.issubset(fields), sorted(required - fields)),
        _check("coverage_cells_exact", len(seen) == len(ANCHOR_FREQUENCIES_GHZ) * PRIMARY_CELLS_PER_ANCHOR, len(seen)),
        _check("coverage_cells_valid", not errors, errors[:20]),
    ]
    return {"checks": checks, "deficits": deficits, "targets": targets}


def _select(
    *, acquisition_mode: str, source_quotas: dict[str, int], accepted: dict[str, Any], candidates: dict[str, Any], coverage: dict[str, Any]
) -> dict[str, Any]:
    candidate_norm = np.asarray(candidates["normalized"], dtype=float)
    accepted_norm = np.asarray(accepted["normalized"], dtype=float)
    components: dict[str, np.ndarray] = {}
    static: dict[str, np.ndarray] = {}
    if acquisition_mode == "ENSEMBLE_ACQUISITION":
        predictions, uncertainties = _prediction_matrices(candidates["rows"])
        components = compute_candidate_components(
            candidate_geometry_normalized=candidate_norm,
            accepted_geometry_normalized=accepted_norm,
            predictions=predictions,
            uncertainties=uncertainties,
            coverage_deficits=coverage["deficits"],
            coverage_targets=coverage["targets"],
        )
        static = source_static_scores(components)
    result = select_source_quotas(
        candidate_geometry_normalized=candidate_norm,
        accepted_geometry_normalized=accepted_norm,
        geometry_hashes=candidates["hashes"],
        source_quotas=source_quotas,
        static_scores=static,
    )
    if not components:
        components = {
            "geometry_novelty": np.asarray(cKDTree(accepted_norm).query(candidate_norm, k=1)[0], dtype=float),
        }
    result["components"] = components
    return result


def _prediction_matrices(rows: list[dict[str, str]]) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    predictions: dict[str, np.ndarray] = {}
    uncertainties: dict[str, np.ndarray] = {}
    for feature in PREDICTION_FEATURES:
        predictions[feature] = np.asarray(
            [[float(row[prediction_column(feature, anchor)]) for anchor in ANCHOR_FREQUENCIES_GHZ] for row in rows],
            dtype=float,
        )
        uncertainties[feature] = np.asarray(
            [[float(row[uncertainty_column(feature, anchor)]) for anchor in ANCHOR_FREQUENCIES_GHZ] for row in rows],
            dtype=float,
        )
    return predictions, uncertainties


def _materialize_selected_rows(
    rows: list[dict[str, str]], analysis: dict[str, Any], *, phase: str, accepted_start: int, accepted_target: int
) -> list[dict[str, Any]]:
    assignments = analysis.get("assignments") or {}
    components = analysis.get("components") or {}
    selected: list[dict[str, Any]] = []
    for selection_rank, index in enumerate(analysis.get("selected_indices") or (), start=1):
        row: dict[str, Any] = dict(rows[int(index)])
        assignment = assignments[int(index)]
        row.update(
            {
                "selection_rank": selection_rank,
                "source_selection_rank": assignment["source_selection_rank"],
                "campaign_phase": phase,
                "acquisition_source": assignment["acquisition_source"],
                "round_accepted_start": accepted_start,
                "round_accepted_target": accepted_target,
                "label_status": "AWAITING_FRESH_REAL_EMX",
                "predictions_are_labels": "false",
                "predicted_coverage_status": (
                    "prediction_only_candidate" if "predicted_local_cells" in components else "NOT_APPLICABLE_NO_PROXY"
                ),
                "selection_score": assignment["selection_score"],
                "batch_diversity_distance": assignment["batch_diversity_distance"],
                "accepted_geometry_novelty_distance": float(components["geometry_novelty"][int(index)]),
            }
        )
        for name in ("deficit_gain", "uncertainty", "boundary_coverage", "feature_validity"):
            row[f"priority__{name}"] = float(components[name][int(index)]) if name in components else ""
        if "predicted_local_cells" in components:
            local_cells = components["predicted_local_cells"][int(index)]
            row["predicted_conditioned_cells_json"] = json.dumps(
                [anchor_index * PRIMARY_CELLS_PER_ANCHOR + int(local) if int(local) >= 0 else None for anchor_index, local in enumerate(local_cells)],
                separators=(",", ":"),
            )
        else:
            row["predicted_conditioned_cells_json"] = ""
        selected.append(row)
    return selected


def _selection_checks(
    analysis: dict[str, Any], rows: list[dict[str, Any]], source_quotas: dict[str, Any], accepted_hashes: set[str]
) -> list[dict[str, Any]]:
    selected_hashes = [str(row.get("geometry_sha256") or "") for row in rows]
    expected = {str(key): _integer(value) for key, value in source_quotas.items()}
    actual = analysis.get("selected_counts_by_source") or {}
    return [
        _check("candidate_selection_completed", analysis.get("selected_count") == ADAPTIVE_BATCH_SIZE, analysis.get("selected_count")),
        _check("selected_queue_count_exact", len(rows) == ADAPTIVE_BATCH_SIZE, len(rows)),
        _check("selected_source_quotas_exact", actual == expected, {"actual": actual, "expected": expected}),
        _check("selected_geometries_unique", len(set(selected_hashes)) == len(rows), len(set(selected_hashes))),
        _check("selected_geometries_disjoint_from_accepted", not (set(selected_hashes) & accepted_hashes), len(set(selected_hashes) & accepted_hashes)),
        _check(
            "selected_rows_are_unlabeled",
            all(row.get("label_status") == "AWAITING_FRESH_REAL_EMX" and row.get("predictions_are_labels") == "false" for row in rows),
            len(rows),
        ),
    ]


def _normalize_geometry(matrix: list[list[float]], bounds: dict[str, Any]) -> tuple[np.ndarray, list[str]]:
    errors: list[str] = []
    array = np.asarray(matrix, dtype=float)
    if array.size == 0:
        return np.empty((0, len(GEOMETRY_FIELDS)), dtype=float), ["geometry matrix is empty"]
    try:
        lower = np.asarray([float(bounds[name][0]) for name in GEOMETRY_FIELDS], dtype=float)
        upper = np.asarray([float(bounds[name][1]) for name in GEOMETRY_FIELDS], dtype=float)
    except (KeyError, TypeError, ValueError, IndexError):
        return np.empty_like(array), ["geometry bounds are invalid"]
    normalized = (array - lower[None, :]) / (upper - lower)[None, :]
    if not np.all(np.isfinite(normalized)):
        errors.append("geometry normalization produced non-finite values")
    outside = np.argwhere((normalized < -1.0e-12) | (normalized > 1.0 + 1.0e-12))
    if outside.size:
        errors.append(f"geometry values outside frozen bounds: {outside[:20].tolist()}")
    return np.clip(normalized, 0.0, 1.0), errors


def _read_csv(path: Path) -> tuple[list[dict[str, str]], set[str]]:
    if not path.is_file():
        return [], set()
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        return rows, set(reader.fieldnames or ())


def _read_json(path: Path, checks: list[dict[str, Any]], name: str) -> dict[str, Any]:
    if not path.is_file():
        checks.append(_check(f"{name}_exists", False, str(path)))
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        checks.append(_check(f"{name}_parses", False, f"{type(exc).__name__}: {exc}"))
        return {}
    checks.append(_check(f"{name}_parses", isinstance(value, dict), type(value).__name__))
    return value if isinstance(value, dict) else {}


def _output_evidence_matches(receipt: dict[str, Any], name: str, path: Path) -> bool:
    outputs = receipt.get("outputs") if isinstance(receipt.get("outputs"), dict) else {}
    evidence = outputs.get(name) if isinstance(outputs.get(name), dict) else {}
    return _evidence_matches(path, str(evidence.get("sha256") or "")) and str(evidence.get("path") or "") == str(path)


def _evidence_matches(path: Path, expected_sha: str) -> bool:
    return path.is_file() and _is_sha256(expected_sha) and _sha256(path) == expected_sha.lower()


def _file_evidence(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.is_file(),
        "size_bytes": path.stat().st_size if path.is_file() else None,
        "sha256": _sha256(path) if path.is_file() else None,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_sha256s(directory: Path) -> None:
    files = sorted(path for path in directory.iterdir() if path.is_file() and path.name != "SHA256SUMS.txt")
    (directory / "SHA256SUMS.txt").write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in files),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _string_digest(values: list[str]) -> str:
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def _integer(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value.lower())


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": detail}


if __name__ == "__main__":
    raise SystemExit(main())
