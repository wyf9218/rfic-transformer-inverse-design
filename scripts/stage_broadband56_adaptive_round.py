#!/usr/bin/env python3
"""Stage one fail-closed Phase-B/C acquisition round.

The script binds the next 5,000-accepted round to the preceding real-EMX audit.
A valid uncertainty-aware ensemble enables the frozen response-repair mixture;
an absent or invalid ensemble falls back to pure maximin exploration without
turning proxy predictions into labels.  This script never launches a solver.
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

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (
    ANCHOR_FREQUENCIES_GHZ,
    CAMPAIGN_ID,
    FREQUENCY_POINTS,
    PRIMARY_CELLS_PER_ANCHOR,
    PRIMARY_FREQUENCY_CONDITIONED_CELLS,
    REQUIRED_CHECKPOINT_COUNTS,
    adaptive_round_for_current_accepted,
    contract_fingerprint,
    prorate_adaptive_source_quotas,
    validate_contract,
)
from rfic_transformer_inverse_design.campaigns.broadband56_adaptive_selection import (
    MINIMUM_CANDIDATE_POOL_FACTOR,
    required_prediction_columns,
    selection_policy_contract,
)

ENSEMBLE_SCHEMA = "broadband56_acquisition_ensemble_receipt_v1"
ADAPTIVE_ROUND_SCHEMA = "broadband56_adaptive_round_contract_v2"
ENSEMBLE_FEATURES = (
    "xp_ohm",
    "xs_ohm",
    "qp",
    "qs",
    "qmin",
    "k_abs",
    "feature_validity_probability",
)
CELL_STATUS_VOCABULARY = (
    "observed",
    "underfilled",
    "unobserved_under_current_geometry_contract",
    "prediction_only_candidate",
    "attempted_but_not_observed",
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    contract_path = Path(args.contract).expanduser().resolve()
    audit_dir = Path(args.audit_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        print(f"refusing to overwrite existing output directory: {out_dir}", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True)

    checks: list[dict[str, Any]] = []
    contract = _read_json(contract_path, checks, "contract_json")
    contract_errors = validate_contract(contract) if contract else ["contract unavailable"]
    checks.append(_check("frozen_contract_is_valid", not contract_errors, contract_errors))
    fingerprint = str(contract.get("contract_fingerprint_sha256") or contract_fingerprint(contract)) if contract else ""

    audit = _validate_preceding_audit(audit_dir, fingerprint, checks)
    checkpoint_accepted = int(audit.get("accepted_geometries") or -1)
    current_accepted = (
        int(args.current_accepted)
        if args.current_accepted is not None
        else checkpoint_accepted
    )
    round_spec = None
    raw_selection_count = 0
    try:
        round_spec, raw_selection_count = adaptive_round_for_current_accepted(
            current_accepted
        )
        checks.extend(
            [
                _check(
                    "checkpoint_is_exact_adaptive_round_start",
                    checkpoint_accepted == round_spec.accepted_start,
                    {
                        "checkpoint_accepted": checkpoint_accepted,
                        "expected": round_spec.accepted_start,
                    },
                ),
                _check(
                    "current_accepted_stays_inside_frozen_round",
                    round_spec.accepted_start
                    <= current_accepted
                    < round_spec.accepted_target,
                    {
                        "current_accepted": current_accepted,
                        "round": round_spec.as_dict(),
                    },
                ),
                _check(
                    "raw_selection_count_closes_exact_round_boundary",
                    raw_selection_count
                    == round_spec.accepted_target - current_accepted,
                    raw_selection_count,
                ),
            ]
        )
    except ValueError as exc:
        checks.append(_check("current_accepted_maps_to_adaptive_round", False, str(exc)))

    ensemble_path = Path(args.ensemble_receipt).expanduser().resolve() if args.ensemble_receipt else None
    ensemble = _validate_ensemble_receipt(
        ensemble_path,
        fingerprint,
        checkpoint_accepted,
        preceding_audit=audit,
    )
    base_pass = bool(checks) and all(item["pass"] for item in checks) and round_spec is not None
    use_ensemble = base_pass and ensemble["status"] == "PASS"
    acquisition_mode = "ENSEMBLE_ACQUISITION" if use_ensemble else "FALLBACK_MAXIMIN"
    if round_spec is not None and raw_selection_count > 0:
        frozen_quotas = (
            round_spec.source_quotas
            if use_ensemble
            else round_spec.fallback_source_quotas
        )
        active_quotas = dict(
            prorate_adaptive_source_quotas(frozen_quotas, raw_selection_count)
        )
    else:
        active_quotas = {}
    status = "PASS" if base_pass else "FAIL"
    decision = (
        "USE_ENSEMBLE_ACQUISITION_FOR_ROUND"
        if status == "PASS" and use_ensemble
        else "USE_MAXIMIN_FALLBACK_FOR_ROUND"
        if status == "PASS"
        else "DO_NOT_BUILD_ADAPTIVE_ROUND"
    )
    round_contract = {
        "schema": ADAPTIVE_ROUND_SCHEMA,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": decision,
        "campaign_id": CAMPAIGN_ID,
        "campaign_contract_fingerprint": fingerprint,
        "round": round_spec.as_dict() if round_spec is not None else None,
        "checkpoint_accepted": checkpoint_accepted,
        "current_accepted": current_accepted,
        "raw_selection_count": raw_selection_count,
        "acquisition_mode": acquisition_mode if status == "PASS" else "NOT_AUTHORIZED",
        "active_source_quotas": active_quotas,
        "candidate_selection_policy": selection_policy_contract(),
        "candidate_pool_requirement": {
            "same_frozen_geometry_bounds": True,
            "canonical_geometry_unique_against_all_accepted": True,
            "analytical_gate": "PASS_REQUIRED_BEFORE_RANKING",
            "topology_gate": "PASS_REQUIRED_BEFORE_RANKING",
            "minimum_pool_factor": MINIMUM_CANDIDATE_POOL_FACTOR,
            "minimum_pool_count": MINIMUM_CANDIDATE_POOL_FACTOR
            * raw_selection_count,
            "minimum_ensemble_members": 5,
            "anchor_frequencies_ghz": list(ANCHOR_FREQUENCIES_GHZ),
            "predicted_features": list(ENSEMBLE_FEATURES),
            "required_prediction_columns": list(required_prediction_columns()) if use_ensemble else [],
            "predictions_are_labels": False,
        },
        "preceding_real_emx_audit": audit,
        "ensemble_gate": ensemble,
        "scientific_boundary": (
            "This contract authorizes candidate priority only. Accepted counts, realized cells, and labels remain "
            "zero until fresh Cadence, zero-blocking Calibre, and real EMX artifacts pass the next audit."
        ),
    }
    round_contract_path = out_dir / "ADAPTIVE_ROUND_CONTRACT.json"
    round_contract_path.write_text(json.dumps(round_contract, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    receipt = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": status,
        "decision": decision,
        "campaign_id": CAMPAIGN_ID,
        "campaign_contract_fingerprint": fingerprint,
        "checks": checks,
        "inputs": {
            "contract": _file_evidence(contract_path),
            "checkpoint_status": _file_evidence(audit_dir / "CHECKPOINT_STATUS.json"),
            "checkpoint_receipt": _file_evidence(audit_dir / "CHECKPOINT_RECEIPT.json"),
            "coverage_cells": _file_evidence(Path(str(audit.get("coverage_cells_path") or ""))),
            "ensemble_receipt": _file_evidence(ensemble_path) if ensemble_path is not None else None,
        },
        "outputs": {"adaptive_round_contract": _file_evidence(round_contract_path)},
    }
    receipt_path = out_dir / "ADAPTIVE_ROUND_RECEIPT.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _write_sha256s(out_dir)

    print(f"overall_status={status}")
    print(f"decision={decision}")
    print(f"acquisition_mode={round_contract['acquisition_mode']}")
    print(f"round_contract={round_contract_path}")
    return 0 if status == "PASS" else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--audit-dir", required=True)
    parser.add_argument("--ensemble-receipt")
    parser.add_argument("--current-accepted", type=int)
    parser.add_argument("--out-dir", required=True)
    return parser.parse_args(argv)


def _validate_preceding_audit(
    audit_dir: Path,
    fingerprint: str,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    status_path = audit_dir / "CHECKPOINT_STATUS.json"
    receipt_path = audit_dir / "CHECKPOINT_RECEIPT.json"
    status = _read_json(status_path, checks, "preceding_checkpoint_status_json")
    receipt = _read_json(receipt_path, checks, "preceding_checkpoint_receipt_json")
    receipt_checks = receipt.get("checks") if isinstance(receipt.get("checks"), list) else []
    accepted = _integer(status.get("accepted_geometries"))
    expected_mode = "checkpoint" if accepted in REQUIRED_CHECKPOINT_COUNTS else "round"
    expected_state = "CHECKPOINT_COMPLETE" if expected_mode == "checkpoint" else f"ROUND_{accepted}_COMPLETE"
    checks.extend(
        [
            _check(
                "preceding_status_is_exact_real_emx_audit",
                status.get("campaign_id") == CAMPAIGN_ID
                and status.get("contract_fingerprint_sha256") == fingerprint
                and status.get("audit_mode") == expected_mode
                and status.get("checkpoint_status") == expected_state
                and accepted >= 0
                and _integer(status.get("s4p_artifacts")) == accepted
                and _integer(status.get("geometry_frequency_rows")) == accepted * FREQUENCY_POINTS,
                status,
            ),
            _check(
                "preceding_receipt_pass",
                receipt.get("overall_status") == "PASS"
                and receipt.get("decision") == "USE_CHECKPOINT"
                and receipt.get("campaign_id") == CAMPAIGN_ID
                and receipt.get("contract_fingerprint_sha256") == fingerprint
                and receipt.get("audit_mode") == expected_mode
                and _integer(receipt.get("expected_accepted")) == accepted
                and bool(receipt_checks)
                and all(isinstance(item, dict) and item.get("pass") is True for item in receipt_checks),
                {
                    "overall_status": receipt.get("overall_status"),
                    "decision": receipt.get("decision"),
                    "audit_mode": receipt.get("audit_mode"),
                    "expected_accepted": receipt.get("expected_accepted"),
                },
            ),
        ]
    )
    outputs = receipt.get("outputs") if isinstance(receipt.get("outputs"), dict) else {}
    inputs = receipt.get("inputs") if isinstance(receipt.get("inputs"), dict) else {}
    status_evidence = outputs.get("checkpoint_status") if isinstance(outputs.get("checkpoint_status"), dict) else {}
    cells_evidence = outputs.get("coverage_cells") if isinstance(outputs.get("coverage_cells"), dict) else {}
    coverage_evidence = outputs.get("coverage_summary") if isinstance(outputs.get("coverage_summary"), dict) else {}
    accepted_evidence = inputs.get("accepted_geometries") if isinstance(inputs.get("accepted_geometries"), dict) else {}
    bounds_evidence = inputs.get("geometry_bounds") if isinstance(inputs.get("geometry_bounds"), dict) else {}
    features_evidence = inputs.get("long_features") if isinstance(inputs.get("long_features"), dict) else {}
    checks.append(_evidence_check("preceding_status_hash_bound", status_path, status_evidence))
    cells_path = Path(str(cells_evidence.get("path") or "")).expanduser().resolve()
    coverage_path = Path(str(coverage_evidence.get("path") or "")).expanduser().resolve()
    accepted_path = Path(str(accepted_evidence.get("path") or "")).expanduser().resolve()
    bounds_path = Path(str(bounds_evidence.get("path") or "")).expanduser().resolve()
    features_path = Path(str(features_evidence.get("path") or "")).expanduser().resolve()
    checks.append(_evidence_check("preceding_coverage_cells_hash_bound", cells_path, cells_evidence))
    checks.append(_evidence_check("preceding_coverage_summary_hash_bound", coverage_path, coverage_evidence))
    checks.append(_evidence_check("preceding_accepted_geometries_hash_bound", accepted_path, accepted_evidence))
    checks.append(_evidence_check("preceding_geometry_bounds_hash_bound", bounds_path, bounds_evidence))
    checks.append(_evidence_check("preceding_long_features_hash_bound", features_path, features_evidence))
    cell_summary = _validate_coverage_cells(cells_path, accepted)
    checks.extend(cell_summary.pop("checks"))
    return {
        "accepted_geometries": accepted,
        "s4p_artifacts": status.get("s4p_artifacts"),
        "geometry_frequency_rows": status.get("geometry_frequency_rows"),
        "coverage_status": status.get("coverage_status"),
        "coverage_cells_path": str(cells_path),
        "coverage_cells_sha256": cells_evidence.get("sha256"),
        "coverage_summary_path": str(coverage_path),
        "coverage_summary_sha256": coverage_evidence.get("sha256"),
        "accepted_geometries_path": str(accepted_path),
        "accepted_geometries_sha256": accepted_evidence.get("sha256"),
        "geometry_bounds_path": str(bounds_path),
        "geometry_bounds_sha256": bounds_evidence.get("sha256"),
        "long_features_path": str(features_path),
        "long_features_sha256": features_evidence.get("sha256"),
        "checkpoint_receipt_path": str(receipt_path),
        "checkpoint_receipt_sha256": _sha256(receipt_path) if receipt_path.is_file() else None,
        "cell_summary": cell_summary,
    }


def _validate_coverage_cells(path: Path, accepted_count: int) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    if not path.is_file():
        checks.append(_check("coverage_cell_table_exists", False, str(path)))
        return {"checks": checks, "row_count": 0}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    required = {
        "anchor_ghz",
        "local_cell_index",
        "conditioned_cell_index",
        "actual_count",
        "target_count",
        "deficit",
        "cell_status",
    }
    fields = set(rows[0]) if rows else set()
    checks.append(_check("coverage_cell_table_columns", required.issubset(fields), sorted(required - fields)))
    expected_target = accepted_count / float(PRIMARY_CELLS_PER_ANCHOR) if accepted_count >= 0 else math.nan
    keys: set[tuple[int, int]] = set()
    errors: list[str] = []
    anchor_totals = {anchor: 0 for anchor in ANCHOR_FREQUENCIES_GHZ}
    for row_index, row in enumerate(rows, start=2):
        anchor = _integer(row.get("anchor_ghz"))
        local = _integer(row.get("local_cell_index"))
        conditioned = _integer(row.get("conditioned_cell_index"))
        actual = _integer(row.get("actual_count"))
        target = _finite(row.get("target_count"))
        deficit = _finite(row.get("deficit"))
        if anchor not in anchor_totals or not 0 <= local < PRIMARY_CELLS_PER_ANCHOR:
            errors.append(f"line {row_index}: invalid anchor/local index")
            continue
        expected_conditioned = ANCHOR_FREQUENCIES_GHZ.index(anchor) * PRIMARY_CELLS_PER_ANCHOR + local
        if conditioned != expected_conditioned:
            errors.append(f"line {row_index}: conditioned index mismatch")
        if actual < 0 or target is None or deficit is None:
            errors.append(f"line {row_index}: invalid count/target/deficit")
        elif not math.isclose(target, expected_target, rel_tol=0.0, abs_tol=1.0e-9):
            errors.append(f"line {row_index}: target_count mismatch")
        elif not math.isclose(deficit, max(target - actual, 0.0), rel_tol=0.0, abs_tol=1.0e-9):
            errors.append(f"line {row_index}: deficit mismatch")
        if str(row.get("cell_status") or "") not in CELL_STATUS_VOCABULARY:
            errors.append(f"line {row_index}: invalid cell status")
        keys.add((anchor, local))
        anchor_totals[anchor] += max(actual, 0)
    checks.extend(
        [
            _check("coverage_cell_table_exact_size", len(rows) == PRIMARY_FREQUENCY_CONDITIONED_CELLS, len(rows)),
            _check("coverage_cell_keys_exact_unique", len(keys) == PRIMARY_FREQUENCY_CONDITIONED_CELLS, len(keys)),
            _check("coverage_cell_values_valid", not errors, errors[:20]),
            _check(
                "coverage_anchor_counts_do_not_exceed_accepted",
                all(total <= accepted_count for total in anchor_totals.values()),
                anchor_totals,
            ),
        ]
    )
    return {"checks": checks, "row_count": len(rows), "anchor_in_envelope_counts": anchor_totals}


def _validate_ensemble_receipt(
    path: Path | None,
    fingerprint: str,
    accepted_count: int,
    *,
    preceding_audit: dict[str, Any],
) -> dict[str, Any]:
    if path is None:
        return {
            "status": "NOT_PROVIDED",
            "decision": "FALLBACK_MAXIMIN",
            "errors": ["no ensemble receipt provided"],
            "receipt": None,
        }
    if not path.is_file():
        return {
            "status": "FAIL",
            "decision": "FALLBACK_MAXIMIN",
            "errors": [f"missing ensemble receipt: {path}"],
            "receipt": _file_evidence(path),
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "FAIL",
            "decision": "FALLBACK_MAXIMIN",
            "errors": [str(exc)],
            "receipt": _file_evidence(path),
        }
    if not isinstance(payload, dict):
        return {
            "status": "FAIL",
            "decision": "FALLBACK_MAXIMIN",
            "errors": ["ensemble receipt top level is not an object"],
            "receipt": _file_evidence(path),
        }
    errors: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            errors.append(message)

    members = payload.get("members") if isinstance(payload.get("members"), list) else []
    seeds = [_integer(member.get("seed")) for member in members if isinstance(member, dict)]
    model_hashes = [str(member.get("model_sha256") or "").lower() for member in members if isinstance(member, dict)]
    require(payload.get("schema") == ENSEMBLE_SCHEMA, "ensemble schema mismatch")
    require(payload.get("overall_status") == "PASS", "ensemble overall_status must be PASS")
    require(payload.get("campaign_id") == CAMPAIGN_ID, "ensemble campaign_id mismatch")
    require(payload.get("campaign_contract_fingerprint") == fingerprint, "ensemble fingerprint mismatch")
    require(payload.get("training_label_source") == "FRESH_REAL_EMX_ONLY", "training labels are not fresh real EMX")
    require(payload.get("split_unit") == "canonical_geometry_sha256", "ensemble split unit is not geometry identity")
    require(payload.get("validation_sealed") is True, "ensemble validation set is not sealed")
    require(payload.get("validation_used_for_training") is False, "sealed validation was used for training")
    require(
        payload.get("validation_used_for_uncertainty_calibration") is False,
        "sealed validation was used for uncertainty calibration",
    )
    require(payload.get("validation_status") == "PASS", "ensemble validation status is not PASS")
    require(payload.get("uncertainty_calibration_status") == "PASS", "ensemble uncertainty is not calibrated")
    require(payload.get("candidate_priority_only") is True, "ensemble is not restricted to candidate priority")
    require(payload.get("predictions_are_final_labels") is False, "ensemble predictions are marked as final labels")
    require(tuple(payload.get("anchor_frequencies_ghz") or ()) == ANCHOR_FREQUENCIES_GHZ, "anchor vector mismatch")
    require(tuple(payload.get("predicted_features") or ()) == ENSEMBLE_FEATURES, "predicted feature vector mismatch")
    require(5 <= len(members) == _integer(payload.get("member_count")), "ensemble must contain at least five members")
    require(len(seeds) == len(set(seeds)) and all(seed >= 0 for seed in seeds), "ensemble seeds are missing or duplicated")
    require(
        len(model_hashes) == len(set(model_hashes)) and all(_is_sha256(value) for value in model_hashes),
        "ensemble model hashes are missing, invalid, or duplicated",
    )
    require(0 < _integer(payload.get("training_geometry_count")) <= accepted_count, "training geometry count is invalid")
    require(_integer(payload.get("calibration_geometry_count")) > 0, "calibration geometry count is invalid")
    require(_integer(payload.get("validation_geometry_count")) > 0, "validation geometry count is invalid")
    require(
        _integer(payload.get("source_accepted_count")) == accepted_count,
        "ensemble source accepted count does not match the preceding audit",
    )
    require(
        _integer(payload.get("training_geometry_count"))
        + _integer(payload.get("calibration_geometry_count"))
        + _integer(payload.get("validation_geometry_count"))
        == accepted_count,
        "ensemble split counts do not close to the preceding accepted count",
    )
    split_identity = payload.get("split_identity_sha256") if isinstance(payload.get("split_identity_sha256"), dict) else {}
    require(
        tuple(split_identity) == ("train", "calibration", "validation")
        and len(set(split_identity.values())) == 3
        and all(_is_sha256(str(value)) for value in split_identity.values()),
        "ensemble split identity hashes are missing, invalid, or duplicated",
    )
    validation = payload.get("validation") if isinstance(payload.get("validation"), dict) else {}
    validation_gates = validation.get("gates") if isinstance(validation.get("gates"), dict) else {}
    require(
        validation.get("overall_status") == "PASS"
        and bool(validation_gates)
        and all(value is True for value in validation_gates.values()),
        "ensemble sealed-validation gates are incomplete or failed",
    )
    calibration = payload.get("uncertainty_calibration") if isinstance(payload.get("uncertainty_calibration"), dict) else {}
    calibration_scales = calibration.get("feature_scales") if isinstance(calibration.get("feature_scales"), dict) else {}
    require(
        tuple(calibration_scales) == ENSEMBLE_FEATURES
        and all(
            (number := _finite(calibration_scales.get(feature))) is not None and number >= 1.0
            for feature in ENSEMBLE_FEATURES
        ),
        "ensemble uncertainty calibration scales are missing or invalid",
    )
    source_bindings = (
        ("source_checkpoint_receipt", "checkpoint_receipt_path", "checkpoint_receipt_sha256"),
        ("source_accepted_geometries", "accepted_geometries_path", "accepted_geometries_sha256"),
        ("source_long_features", "long_features_path", "long_features_sha256"),
        ("source_geometry_bounds", "geometry_bounds_path", "geometry_bounds_sha256"),
    )
    for payload_key, path_key, sha_key in source_bindings:
        evidence = payload.get(payload_key) if isinstance(payload.get(payload_key), dict) else {}
        expected_path = Path(str(preceding_audit.get(path_key) or "")).expanduser().resolve()
        require(
            str(evidence.get("path") or "") == str(expected_path)
            and str(evidence.get("sha256") or "").lower()
            == str(preceding_audit.get(sha_key) or "").lower()
            and _evidence_matches(expected_path, evidence),
            f"ensemble {payload_key} does not bind to the preceding audit",
        )
    training = payload.get("training_table") if isinstance(payload.get("training_table"), dict) else {}
    training_path = Path(str(training.get("path") or "")).expanduser().resolve()
    require(_evidence_matches(training_path, training), "training table file/hash evidence mismatch")
    for index, member in enumerate(members):
        evidence = member.get("model_file") if isinstance(member, dict) and isinstance(member.get("model_file"), dict) else {}
        model_path = Path(str(evidence.get("path") or "")).expanduser().resolve()
        require(_evidence_matches(model_path, evidence), f"member {index} model file/hash evidence mismatch")
        if isinstance(member, dict):
            require(str(member.get("model_sha256") or "").lower() == evidence.get("sha256"), f"member {index} model SHA mismatch")
            require(
                _integer(member.get("training_geometry_count"))
                == _integer(payload.get("training_geometry_count")),
                f"member {index} training geometry count mismatch",
            )
    return {
        "status": "PASS" if not errors else "FAIL",
        "decision": "USE_ENSEMBLE" if not errors else "FALLBACK_MAXIMIN",
        "errors": errors,
        "receipt": _file_evidence(path),
        "member_count": len(members),
        "training_geometry_count": payload.get("training_geometry_count"),
        "validation_geometry_count": payload.get("validation_geometry_count"),
    }


def _read_json(path: Path, checks: list[dict[str, Any]], name: str) -> dict[str, Any]:
    if not path.is_file():
        checks.append(_check(name, False, f"missing: {path}"))
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        checks.append(_check(name, False, str(exc)))
        return {}
    checks.append(_check(name, isinstance(payload, dict), str(path)))
    return payload if isinstance(payload, dict) else {}


def _evidence_check(name: str, path: Path, evidence: dict[str, Any]) -> dict[str, Any]:
    return _check(name, _evidence_matches(path, evidence), evidence)


def _evidence_matches(path: Path, evidence: dict[str, Any]) -> bool:
    if not path.is_file() or not isinstance(evidence, dict):
        return False
    evidence_path = Path(str(evidence.get("path") or "")).expanduser().resolve()
    return evidence_path == path and str(evidence.get("sha256") or "").lower() == _sha256(path)


def _integer(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": detail}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_evidence(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    return {
        "path": str(path),
        "exists": path.is_file(),
        "size_bytes": path.stat().st_size if path.is_file() else None,
        "sha256": _sha256(path) if path.is_file() else None,
    }


def _write_sha256s(out_dir: Path) -> None:
    lines = []
    for path in sorted(out_dir.iterdir()):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            lines.append(f"{_sha256(path)}  {path.name}")
    (out_dir / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
