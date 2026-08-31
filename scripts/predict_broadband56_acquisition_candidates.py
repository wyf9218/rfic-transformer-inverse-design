#!/usr/bin/env python3
"""Attach calibrated ensemble predictions to one adaptive candidate pool.

The predictions are candidate-priority metadata only.  The output contains no
fresh EMX labels and cannot increment accepted or coverage counts.
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
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rfic_transformer_inverse_design.campaigns.broadband56_acquisition_ensemble import (  # noqa: E402
    CONTINUOUS_FEATURES,
    ENSEMBLE_RECEIPT_SCHEMA,
    MINIMUM_MEMBERS,
    PREDICTED_FEATURES,
    ensemble_mean_and_uncertainty,
    load_member,
    predict_member,
)
from rfic_transformer_inverse_design.campaigns.broadband56_adaptive_selection import (  # noqa: E402
    MINIMUM_CANDIDATE_POOL_FACTOR,
    prediction_column,
    required_prediction_columns,
    selection_policy_contract,
    uncertainty_column,
)
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    ADAPTIVE_BATCH_SIZE,
    ANCHOR_FREQUENCIES_GHZ,
    CAMPAIGN_ID,
    GEOMETRY_FIELDS,
    canonical_geometry_sha256,
    contract_fingerprint,
    validate_contract,
)
from rfic_transformer_inverse_design.campaigns.broadband56_geometry_coverage import (  # noqa: E402
    validate_geometry_bounds_payload,
)


CANDIDATE_GATE_FIELDS = ("analytical_status", "topology_status", "top_metal_drc_status")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    contract_path = Path(args.contract).expanduser().resolve()
    round_dir = Path(args.round_dir).expanduser().resolve()
    ensemble_path = Path(args.ensemble_receipt).expanduser().resolve()
    candidate_path = Path(args.candidate_csv).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        print(f"refusing to overwrite existing output directory: {out_dir}", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True)

    checks: list[dict[str, Any]] = []
    contract = _read_json(contract_path, checks, "campaign_contract")
    contract_errors = validate_contract(contract) if contract else ["contract unavailable"]
    checks.append(_check("campaign_contract_valid", not contract_errors, contract_errors))
    fingerprint = str(contract.get("contract_fingerprint_sha256") or contract_fingerprint(contract)) if contract else ""

    round_contract_path = round_dir / "ADAPTIVE_ROUND_CONTRACT.json"
    round_receipt_path = round_dir / "ADAPTIVE_ROUND_RECEIPT.json"
    round_contract = _read_json(round_contract_path, checks, "adaptive_round_contract")
    round_receipt = _read_json(round_receipt_path, checks, "adaptive_round_receipt")
    round_info = round_contract.get("round") if isinstance(round_contract.get("round"), dict) else {}
    preceding = round_contract.get("preceding_real_emx_audit") if isinstance(round_contract.get("preceding_real_emx_audit"), dict) else {}
    ensemble_gate = round_contract.get("ensemble_gate") if isinstance(round_contract.get("ensemble_gate"), dict) else {}
    ensemble_gate_evidence = ensemble_gate.get("receipt") if isinstance(ensemble_gate.get("receipt"), dict) else {}
    accepted_start = _integer(round_info.get("accepted_start"))
    raw_selection_count = _integer(round_contract.get("raw_selection_count"))
    checks.extend(
        [
            _check(
                "adaptive_round_authorizes_ensemble",
                round_contract.get("overall_status") == "PASS"
                and round_contract.get("campaign_id") == CAMPAIGN_ID
                and round_contract.get("campaign_contract_fingerprint") == fingerprint
                and round_contract.get("acquisition_mode") == "ENSEMBLE_ACQUISITION"
                and round_contract.get("candidate_selection_policy") == selection_policy_contract(),
                round_contract.get("decision"),
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
                "exact_ensemble_receipt_authorized",
                _evidence_matches(ensemble_path, ensemble_gate_evidence),
                str(ensemble_path),
            ),
        ]
    )

    ensemble = _read_json(ensemble_path, checks, "ensemble_receipt")
    ensemble_errors = _validate_ensemble_receipt(ensemble, ensemble_path, fingerprint, accepted_start)
    checks.append(_check("ensemble_receipt_valid", not ensemble_errors, ensemble_errors))
    calibration = ensemble.get("uncertainty_calibration") if isinstance(ensemble.get("uncertainty_calibration"), dict) else {}

    bounds_path = Path(str(preceding.get("geometry_bounds_path") or "")).expanduser().resolve()
    accepted_path = Path(str(preceding.get("accepted_geometries_path") or "")).expanduser().resolve()
    checks.extend(
        [
            _check(
                "geometry_bounds_hash_bound",
                _path_sha_matches(bounds_path, str(preceding.get("geometry_bounds_sha256") or "")),
                str(bounds_path),
            ),
            _check(
                "accepted_ledger_hash_bound",
                _path_sha_matches(accepted_path, str(preceding.get("accepted_geometries_sha256") or "")),
                str(accepted_path),
            ),
        ]
    )
    bounds_payload = _read_json(bounds_path, checks, "geometry_bounds")
    bounds_errors = (
        validate_geometry_bounds_payload(bounds_payload, contract_fingerprint_sha256=fingerprint)
        if bounds_payload
        else ["geometry bounds unavailable"]
    )
    checks.append(_check("geometry_bounds_valid", not bounds_errors, bounds_errors))
    bounds = bounds_payload.get("field_bounds_um") if isinstance(bounds_payload.get("field_bounds_um"), dict) else {}
    accepted_hashes = _read_accepted_hashes(accepted_path)
    checks.append(
        _check(
            "accepted_ledger_count_exact",
            len(accepted_hashes) == accepted_start,
            {"actual": len(accepted_hashes), "expected": accepted_start},
        )
    )

    candidates = _load_candidates(
        candidate_path,
        fingerprint=fingerprint,
        bounds=bounds,
        accepted_hashes=accepted_hashes,
    )
    checks.extend(candidates.pop("checks"))
    checks.append(
        _check(
            "candidate_pool_minimum_size",
            1 <= raw_selection_count <= ADAPTIVE_BATCH_SIZE
            and int(candidates.get("count") or 0)
            >= MINIMUM_CANDIDATE_POOL_FACTOR * raw_selection_count,
            {
                "actual": candidates.get("count"),
                "minimum": MINIMUM_CANDIDATE_POOL_FACTOR * raw_selection_count,
            },
        )
    )

    members = []
    if all(item["pass"] for item in checks):
        try:
            for member_evidence in ensemble["members"]:
                path = Path(str(member_evidence["model_file"]["path"])).expanduser().resolve()
                member = load_member(path)
                members.append(member)
                expected_seed = _integer(member_evidence.get("seed"))
                expected_training_count = _integer(member_evidence.get("training_geometry_count"))
                expected_hidden_features = _integer(member_evidence.get("hidden_features"))
                expected_ridge = _float(member_evidence.get("ridge"))
                if (
                    member.seed != expected_seed
                    or member.training_geometry_count != expected_training_count
                    or member.hidden_features != expected_hidden_features
                    or not math.isclose(member.ridge, expected_ridge, rel_tol=0.0, abs_tol=1.0e-15)
                ):
                    raise ValueError("ensemble model metadata differs from its receipt evidence")
            checks.append(_check("ensemble_member_metadata_bound", True, len(members)))
        except (KeyError, OSError, TypeError, ValueError) as exc:
            checks.append(_check("ensemble_members_load", False, f"{type(exc).__name__}: {exc}"))

    output_rows: list[dict[str, Any]] = []
    ensemble_sha = _sha256(ensemble_path) if ensemble_path.is_file() else ""
    if all(item["pass"] for item in checks):
        try:
            output_rows = _predict_rows(
                candidates["rows"],
                candidates["normalized"],
                members,
                calibration,
                ensemble_sha=ensemble_sha,
                batch_size=int(args.batch_size),
            )
            checks.extend(
                [
                    _check("prediction_row_count_exact", len(output_rows) == candidates["count"], len(output_rows)),
                    _check("prediction_columns_exact", _prediction_columns_present(output_rows), len(output_rows)),
                    _check("predictions_are_not_labels", all(row.get("predictions_are_labels") == "false" for row in output_rows), len(output_rows)),
                ]
            )
        except (KeyError, TypeError, ValueError) as exc:
            checks.append(_check("candidate_prediction_completed", False, f"{type(exc).__name__}: {exc}"))

    overall = "PASS" if checks and all(item["pass"] for item in checks) else "FAIL"
    prediction_path = out_dir / "broadband56_candidate_pool_with_ensemble_predictions.csv"
    if overall == "PASS":
        _write_csv(prediction_path, output_rows)
    summary_path = out_dir / "CANDIDATE_PREDICTION_SUMMARY.json"
    summary = {
        "schema": "broadband56_candidate_prediction_summary_v1",
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall,
        "decision": "USE_FOR_ADAPTIVE_CANDIDATE_SELECTION_ONLY" if overall == "PASS" else "DO_NOT_USE_CANDIDATE_PREDICTIONS",
        "campaign_id": CAMPAIGN_ID,
        "campaign_contract_fingerprint": fingerprint,
        "accepted_start": accepted_start,
        "candidate_count": len(output_rows),
        "member_count": len(members),
        "prediction_columns": list(required_prediction_columns()),
        "inputs": {
            "campaign_contract": _file_evidence(contract_path),
            "adaptive_round_contract": _file_evidence(round_contract_path),
            "adaptive_round_receipt": _file_evidence(round_receipt_path),
            "ensemble_receipt": _file_evidence(ensemble_path),
            "candidate_pool": _file_evidence(candidate_path),
            "accepted_geometries": _file_evidence(accepted_path),
            "geometry_bounds": _file_evidence(bounds_path),
        },
        "output": _file_evidence(prediction_path),
        "checks": checks,
        "scientific_boundary": (
            "Every pred__/unc__ value is candidate-priority provenance only. It is not an EMX label, accepted row, "
            "realized coverage count, execution result, or physical validation."
        ),
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    receipt_path = out_dir / "CANDIDATE_PREDICTION_RECEIPT.json"
    receipt = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall,
        "decision": summary["decision"],
        "campaign_id": CAMPAIGN_ID,
        "campaign_contract_fingerprint": fingerprint,
        "checks": checks,
        "outputs": {
            "prediction_summary": _file_evidence(summary_path),
            "candidate_predictions": _file_evidence(prediction_path) if prediction_path.is_file() else None,
        },
    }
    receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _write_sha256s(out_dir)
    print(f"overall_status={overall}")
    print(f"decision={summary['decision']}")
    print(f"candidate_count={len(output_rows)}")
    if prediction_path.is_file():
        print(f"candidate_predictions={prediction_path}")
    print(f"receipt={receipt_path}")
    return 0 if overall == "PASS" else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--round-dir", required=True)
    parser.add_argument("--ensemble-receipt", required=True)
    parser.add_argument("--candidate-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--batch-size", type=int, default=4096)
    args = parser.parse_args(argv)
    if int(args.batch_size) < 1:
        parser.error("--batch-size must be positive")
    return args


def _validate_ensemble_receipt(
    payload: dict[str, Any], path: Path, fingerprint: str, accepted_start: int
) -> list[str]:
    errors: list[str] = []
    members = payload.get("members") if isinstance(payload.get("members"), list) else []
    seeds: list[int] = []
    hashes: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            errors.append(message)

    require(path.is_file(), "ensemble receipt is missing")
    require(payload.get("schema") == ENSEMBLE_RECEIPT_SCHEMA, "ensemble schema mismatch")
    require(payload.get("overall_status") == "PASS", "ensemble receipt is not PASS")
    require(payload.get("campaign_id") == CAMPAIGN_ID, "ensemble campaign mismatch")
    require(payload.get("campaign_contract_fingerprint") == fingerprint, "ensemble fingerprint mismatch")
    require(_integer(payload.get("source_accepted_count")) == accepted_start, "ensemble accepted-count mismatch")
    require(payload.get("training_label_source") == "FRESH_REAL_EMX_ONLY", "ensemble label source mismatch")
    require(payload.get("split_unit") == "canonical_geometry_sha256", "ensemble split unit mismatch")
    require(payload.get("validation_sealed") is True, "ensemble validation is not sealed")
    require(payload.get("validation_used_for_training") is False, "sealed validation was used for training")
    require(payload.get("validation_used_for_uncertainty_calibration") is False, "sealed validation was used for calibration")
    require(payload.get("validation_status") == "PASS", "ensemble validation did not pass")
    require(payload.get("uncertainty_calibration_status") == "PASS", "ensemble uncertainty calibration did not pass")
    require(payload.get("candidate_priority_only") is True, "ensemble is not candidate-priority-only")
    require(payload.get("predictions_are_final_labels") is False, "ensemble predictions are marked as labels")
    require(tuple(payload.get("anchor_frequencies_ghz") or ()) == ANCHOR_FREQUENCIES_GHZ, "ensemble anchor mismatch")
    require(tuple(payload.get("predicted_features") or ()) == PREDICTED_FEATURES, "ensemble feature mismatch")
    require(len(members) == _integer(payload.get("member_count")) and len(members) >= MINIMUM_MEMBERS, "ensemble member count mismatch")
    require(
        _integer(payload.get("training_geometry_count"))
        + _integer(payload.get("calibration_geometry_count"))
        + _integer(payload.get("validation_geometry_count"))
        == accepted_start,
        "ensemble split counts do not close to accepted count",
    )
    for index, member in enumerate(members):
        if not isinstance(member, dict):
            errors.append(f"member {index} is not an object")
            continue
        seed = _integer(member.get("seed"))
        digest = str(member.get("model_sha256") or "").lower()
        evidence = member.get("model_file") if isinstance(member.get("model_file"), dict) else {}
        model_path = Path(str(evidence.get("path") or "")).expanduser().resolve()
        seeds.append(seed)
        hashes.append(digest)
        require(seed >= 0, f"member {index} seed is invalid")
        require(_evidence_matches(model_path, evidence), f"member {index} model evidence mismatch")
        require(digest == str(evidence.get("sha256") or "").lower(), f"member {index} model SHA mismatch")
    require(len(set(seeds)) == len(seeds), "ensemble seeds are duplicated")
    require(len(set(hashes)) == len(hashes), "ensemble model hashes are duplicated")
    calibration = payload.get("uncertainty_calibration") if isinstance(payload.get("uncertainty_calibration"), dict) else {}
    scales = calibration.get("feature_scales") if isinstance(calibration.get("feature_scales"), dict) else {}
    require(
        tuple(scales) == PREDICTED_FEATURES
        and all(math.isfinite(_float(scales.get(feature))) and _float(scales.get(feature)) >= 1.0 for feature in PREDICTED_FEATURES),
        "ensemble calibration scales are missing or invalid",
    )
    return errors


def _load_candidates(
    path: Path,
    *,
    fingerprint: str,
    bounds: dict[str, Any],
    accepted_hashes: set[str],
) -> dict[str, Any]:
    rows, fields = _read_csv(path)
    required = {
        "candidate_id",
        "campaign_id",
        "campaign_contract_fingerprint",
        "geometry_sha256",
        "candidate_generation_mode",
        "candidate_generation_seed",
        *CANDIDATE_GATE_FIELDS,
        *(f"geom__{name}" for name in GEOMETRY_FIELDS),
    }
    errors: list[str] = []
    ids: list[str] = []
    hashes: list[str] = []
    matrix: list[list[float]] = []
    for row_index, row in enumerate(rows, start=2):
        candidate_id = str(row.get("candidate_id") or "").strip()
        digest = str(row.get("geometry_sha256") or "").strip().lower()
        try:
            values = [float(row[f"geom__{name}"]) for name in GEOMETRY_FIELDS]
        except (KeyError, TypeError, ValueError):
            errors.append(f"line {row_index}: invalid geometry values")
            continue
        geometry = {name: value for name, value in zip(GEOMETRY_FIELDS, values)}
        if not candidate_id or digest != canonical_geometry_sha256(geometry):
            errors.append(f"line {row_index}: candidate identity mismatch")
        if digest in accepted_hashes:
            errors.append(f"line {row_index}: candidate repeats accepted geometry")
        if row.get("campaign_id") != CAMPAIGN_ID or row.get("campaign_contract_fingerprint") != fingerprint:
            errors.append(f"line {row_index}: campaign identity mismatch")
        if any(str(row.get(field) or "").upper() != "PASS" for field in CANDIDATE_GATE_FIELDS):
            errors.append(f"line {row_index}: analytical/topology gate is not PASS")
        if _integer(row.get("candidate_generation_seed")) < 0 or not str(row.get("candidate_generation_mode") or ""):
            errors.append(f"line {row_index}: candidate generation provenance is missing")
        if any(column in row and str(row.get(column) or "") for column in required_prediction_columns()):
            errors.append(f"line {row_index}: candidate pool already contains prediction columns")
        ids.append(candidate_id)
        hashes.append(digest)
        matrix.append(values)
    normalized, normalization_errors = _normalize_geometry(matrix, bounds)
    errors.extend(normalization_errors)
    checks = [
        _check("candidate_pool_exists", path.is_file(), str(path)),
        _check("candidate_columns_present", required.issubset(fields), sorted(required - fields)),
        _check("candidate_rows_present", bool(rows), len(rows)),
        _check("candidate_rows_valid", not errors, errors[:20]),
        _check("candidate_ids_unique", len(set(ids)) == len(rows), len(set(ids))),
        _check("candidate_hashes_unique", len(set(hashes)) == len(rows), len(set(hashes))),
    ]
    return {"checks": checks, "rows": rows, "normalized": normalized, "count": len(rows)}


def _predict_rows(
    rows: list[dict[str, str]],
    normalized: np.ndarray,
    members: list[Any],
    calibration: dict[str, Any],
    *,
    ensemble_sha: str,
    batch_size: int,
) -> list[dict[str, Any]]:
    if len(members) < MINIMUM_MEMBERS or batch_size < 1:
        raise ValueError("member count or prediction batch size is invalid")
    output: list[dict[str, Any]] = []
    qmin_index = CONTINUOUS_FEATURES.index("qmin")
    qp_index = CONTINUOUS_FEATURES.index("qp")
    qs_index = CONTINUOUS_FEATURES.index("qs")
    for start in range(0, len(rows), batch_size):
        stop = min(len(rows), start + batch_size)
        member_predictions = np.stack(
            [predict_member(member, normalized[start:stop]) for member in members]
        )
        mean, uncertainty = ensemble_mean_and_uncertainty(member_predictions, calibration)
        mean[:, :, qmin_index] = np.minimum(mean[:, :, qp_index], mean[:, :, qs_index])
        for local_index, source_row in enumerate(rows[start:stop]):
            row: dict[str, Any] = dict(source_row)
            row["ensemble_receipt_sha256"] = ensemble_sha
            row["prediction_provenance"] = "temporary_acquisition_ensemble_candidate_priority_only"
            row["predictions_are_labels"] = "false"
            row["label_status"] = "UNEVALUATED_AWAITING_FRESH_REAL_EMX"
            for anchor_index, anchor in enumerate(ANCHOR_FREQUENCIES_GHZ):
                for feature_index, feature in enumerate(PREDICTED_FEATURES):
                    row[prediction_column(feature, anchor)] = float(mean[local_index, anchor_index, feature_index])
                    row[uncertainty_column(feature, anchor)] = float(uncertainty[local_index, anchor_index, feature_index])
            output.append(row)
    return output


def _prediction_columns_present(rows: list[dict[str, Any]]) -> bool:
    if not rows:
        return False
    required = required_prediction_columns()
    for row in rows:
        if any(not math.isfinite(_float(row.get(column))) for column in required):
            return False
        for anchor in ANCHOR_FREQUENCIES_GHZ:
            qp = _float(row[prediction_column("qp", anchor)])
            qs = _float(row[prediction_column("qs", anchor)])
            qmin = _float(row[prediction_column("qmin", anchor)])
            validity = _float(row[prediction_column("feature_validity_probability", anchor)])
            if not math.isclose(qmin, min(qp, qs), rel_tol=0.0, abs_tol=1.0e-6):
                return False
            if not 0.0 <= validity <= 1.0:
                return False
            if any(_float(row[uncertainty_column(feature, anchor)]) < 0.0 for feature in PREDICTED_FEATURES):
                return False
    return True


def _read_accepted_hashes(path: Path) -> set[str]:
    rows, _ = _read_csv(path)
    return {str(row.get("geometry_sha256") or "").strip().lower() for row in rows if row.get("geometry_sha256")}


def _normalize_geometry(matrix: list[list[float]], bounds: dict[str, Any]) -> tuple[np.ndarray, list[str]]:
    array = np.asarray(matrix, dtype=float)
    if array.size == 0:
        return np.empty((0, len(GEOMETRY_FIELDS))), ["candidate geometry matrix is empty"]
    errors: list[str] = []
    try:
        lower = np.asarray([float(bounds[name][0]) for name in GEOMETRY_FIELDS])
        upper = np.asarray([float(bounds[name][1]) for name in GEOMETRY_FIELDS])
    except (KeyError, TypeError, ValueError, IndexError):
        return np.empty_like(array), ["geometry bounds are invalid"]
    normalized = (array - lower[None, :]) / (upper - lower)[None, :]
    outside = np.argwhere((normalized < -1.0e-12) | (normalized > 1.0 + 1.0e-12))
    if outside.size:
        errors.append(f"candidate geometries outside frozen bounds: {outside[:20].tolist()}")
    if not np.all(np.isfinite(normalized)):
        errors.append("candidate geometry normalization is non-finite")
    return np.clip(normalized, 0.0, 1.0), errors


def _output_evidence_matches(receipt: dict[str, Any], name: str, path: Path) -> bool:
    outputs = receipt.get("outputs") if isinstance(receipt.get("outputs"), dict) else {}
    evidence = outputs.get(name) if isinstance(outputs.get(name), dict) else {}
    return _evidence_matches(path, evidence)


def _evidence_matches(path: Path, evidence: dict[str, Any]) -> bool:
    return (
        path.is_file()
        and str(evidence.get("path") or "") == str(path)
        and str(evidence.get("sha256") or "").lower() == _sha256(path)
    )


def _path_sha_matches(path: Path, expected_sha: str) -> bool:
    return path.is_file() and len(expected_sha) == 64 and _sha256(path) == expected_sha.lower()


def _read_csv(path: Path) -> tuple[list[dict[str, str]], set[str]]:
    if not path.is_file():
        return [], set()
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        return rows, set(reader.fieldnames or ())


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


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


def _file_evidence(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.is_file(),
        "size_bytes": path.stat().st_size if path.is_file() else None,
        "sha256": _sha256(path) if path.is_file() else None,
    }


def _write_sha256s(directory: Path) -> None:
    files = sorted(path for path in directory.iterdir() if path.is_file() and path.name != "SHA256SUMS.txt")
    (directory / "SHA256SUMS.txt").write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in files), encoding="utf-8"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _integer(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": str(name), "pass": bool(passed), "detail": detail}


if __name__ == "__main__":
    raise SystemExit(main())
