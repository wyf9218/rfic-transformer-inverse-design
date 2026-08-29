#!/usr/bin/env python3
"""Train one checkpoint-bound temporary acquisition ensemble.

Only hash-bound accepted geometries and fresh-real-EMX feature rows from a
PASS broadband56 checkpoint may be read.  The models are candidate-priority
tools and never become final labels or physical evidence.
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
    DEFAULT_CALIBRATION_FRACTION,
    DEFAULT_HIDDEN_FEATURES,
    DEFAULT_INTERVAL_TARGET,
    DEFAULT_MEMBER_SEEDS,
    DEFAULT_RIDGE,
    DEFAULT_TRAIN_FRACTION,
    ENSEMBLE_RECEIPT_SCHEMA,
    MINIMUM_MEMBERS,
    PREDICTED_FEATURES,
    deterministic_geometry_split,
    evaluate_ensemble,
    fit_random_feature_member,
    fit_uncertainty_calibration,
    predict_member,
    save_member,
)
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    ANCHOR_FREQUENCIES_GHZ,
    CAMPAIGN_ID,
    FREQUENCY_POINTS,
    GEOMETRY_FIELDS,
    adaptive_round_spec,
    canonical_geometry_sha256,
    contract_fingerprint,
    validate_contract,
)
from rfic_transformer_inverse_design.campaigns.broadband56_geometry_coverage import (  # noqa: E402
    validate_geometry_bounds_payload,
)


MINIMUM_ACQUISITION_TRAINING_COUNT = 50_000


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    contract_path = Path(args.contract).expanduser().resolve()
    audit_dir = Path(args.checkpoint_audit_dir).expanduser().resolve()
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

    checkpoint = _load_checkpoint(audit_dir, fingerprint)
    checks.extend(checkpoint.pop("checks"))
    accepted_count = int(checkpoint.get("accepted_count") or 0)
    try:
        round_spec = adaptive_round_spec(accepted_count)
    except ValueError as exc:
        round_spec = None
        checks.append(_check("accepted_count_is_adaptive_round_start", False, str(exc)))
    else:
        checks.append(_check("accepted_count_is_adaptive_round_start", True, round_spec.as_dict()))
    checks.append(
        _check(
            "sufficient_real_emx_training_count",
            accepted_count >= MINIMUM_ACQUISITION_TRAINING_COUNT,
            {"actual": accepted_count, "minimum": MINIMUM_ACQUISITION_TRAINING_COUNT},
        )
    )

    bounds_payload = _read_json(Path(str(checkpoint.get("geometry_bounds_path") or "")), checks, "geometry_bounds")
    bounds_errors = (
        validate_geometry_bounds_payload(bounds_payload, contract_fingerprint_sha256=fingerprint)
        if bounds_payload
        else ["geometry bounds unavailable"]
    )
    checks.append(_check("geometry_bounds_valid", not bounds_errors, bounds_errors))
    bounds = bounds_payload.get("field_bounds_um") if isinstance(bounds_payload.get("field_bounds_um"), dict) else {}

    accepted: dict[str, Any] = {}
    features: dict[str, Any] = {}
    if all(item["pass"] for item in checks):
        accepted = _load_accepted(
            Path(str(checkpoint["accepted_geometries_path"])),
            fingerprint=fingerprint,
            expected_count=accepted_count,
            bounds=bounds,
        )
        checks.extend(accepted.pop("checks"))
    if all(item["pass"] for item in checks):
        features = _load_anchor_features(
            Path(str(checkpoint["long_features_path"])),
            geometry_ids=accepted["geometry_ids"],
            geometry_hashes=accepted["geometry_hashes"],
            fingerprint=fingerprint,
        )
        checks.extend(features.pop("checks"))

    seeds = tuple(int(value) for value in (args.member_seed or DEFAULT_MEMBER_SEEDS))
    checks.extend(
        [
            _check("minimum_member_count", len(seeds) >= MINIMUM_MEMBERS, len(seeds)),
            _check("member_seeds_unique_nonnegative", len(set(seeds)) == len(seeds) and all(value >= 0 for value in seeds), seeds),
            _check("hidden_features_positive", int(args.hidden_features) >= 4, args.hidden_features),
            _check("ridge_positive", math.isfinite(float(args.ridge)) and float(args.ridge) > 0.0, args.ridge),
        ]
    )

    split = None
    members: list[dict[str, Any]] = []
    calibration: dict[str, Any] = {}
    validation: dict[str, Any] = {"overall_status": "NOT_RUN"}
    split_manifest_path = out_dir / "ensemble_geometry_split_manifest.csv"
    if all(item["pass"] for item in checks):
        try:
            split = deterministic_geometry_split(
                accepted["geometry_hashes"],
                split_seed=int(args.split_seed),
                train_fraction=float(args.train_fraction),
                calibration_fraction=float(args.calibration_fraction),
            )
            _write_split_manifest(split_manifest_path, accepted, split)
            trained = []
            for member_index, seed in enumerate(seeds):
                member = fit_random_feature_member(
                    geometry_normalized=accepted["normalized"],
                    continuous_targets=features["continuous"],
                    validity_targets=features["validity"],
                    train_indices=split.train_indices,
                    seed=seed,
                    hidden_features=int(args.hidden_features),
                    ridge=float(args.ridge),
                    bootstrap_fraction=float(args.bootstrap_fraction),
                )
                model_path = out_dir / f"acquisition_member_{member_index:02d}_seed_{seed}.npz"
                save_member(model_path, member)
                trained.append(member)
                members.append(
                    {
                        "member_index": member_index,
                        "seed": seed,
                        "model_sha256": _sha256(model_path),
                        "model_file": _file_evidence(model_path),
                        "hidden_features": member.hidden_features,
                        "ridge": member.ridge,
                        "training_geometry_count": member.training_geometry_count,
                        "valid_training_counts_by_anchor": member.valid_training_counts.astype(int).tolist(),
                    }
                )

            calibration_predictions = np.stack(
                [predict_member(member, accepted["normalized"][split.calibration_indices]) for member in trained]
            )
            calibration = fit_uncertainty_calibration(
                member_predictions=calibration_predictions,
                continuous_targets=features["continuous"][split.calibration_indices],
                validity_targets=features["validity"][split.calibration_indices],
                interval_target=float(args.interval_target),
            )
            validation_predictions = np.stack(
                [predict_member(member, accepted["normalized"][split.validation_indices]) for member in trained]
            )
            validation = evaluate_ensemble(
                member_predictions=validation_predictions,
                continuous_targets=features["continuous"][split.validation_indices],
                validity_targets=features["validity"][split.validation_indices],
                calibration=calibration,
            )
            checks.append(_check("sealed_validation_pass", validation["overall_status"] == "PASS", validation))
        except (KeyError, OSError, TypeError, ValueError, np.linalg.LinAlgError) as exc:
            checks.append(_check("ensemble_training_completed", False, f"{type(exc).__name__}: {exc}"))

    overall = "PASS" if checks and all(item["pass"] for item in checks) else "FAIL"
    training_count = len(split.train_indices) if split is not None else 0
    calibration_count = len(split.calibration_indices) if split is not None else 0
    validation_count = len(split.validation_indices) if split is not None else 0
    receipt_path = out_dir / "ENSEMBLE_RECEIPT.json"
    receipt = {
        "schema": ENSEMBLE_RECEIPT_SCHEMA,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall,
        "decision": "USE_FOR_CANDIDATE_PRIORITY_ONLY" if overall == "PASS" else "FALLBACK_TO_MAXIMIN",
        "campaign_id": CAMPAIGN_ID,
        "campaign_contract_fingerprint": fingerprint,
        "source_accepted_count": accepted_count,
        "source_checkpoint_receipt": _file_evidence(audit_dir / "CHECKPOINT_RECEIPT.json"),
        "source_checkpoint_status": _file_evidence(audit_dir / "CHECKPOINT_STATUS.json"),
        "source_accepted_geometries": _file_evidence(Path(str(checkpoint.get("accepted_geometries_path") or ""))),
        "source_long_features": _file_evidence(Path(str(checkpoint.get("long_features_path") or ""))),
        "source_geometry_bounds": _file_evidence(Path(str(checkpoint.get("geometry_bounds_path") or ""))),
        "training_label_source": "FRESH_REAL_EMX_ONLY",
        "split_unit": "canonical_geometry_sha256",
        "split_seed": int(args.split_seed),
        "training_geometry_count": training_count,
        "calibration_geometry_count": calibration_count,
        "validation_geometry_count": validation_count,
        "split_identity_sha256": {
            "train": split.train_hash_sha256 if split is not None else None,
            "calibration": split.calibration_hash_sha256 if split is not None else None,
            "validation": split.validation_hash_sha256 if split is not None else None,
        },
        "training_table": _file_evidence(split_manifest_path),
        "validation_sealed": True,
        "validation_used_for_training": False,
        "validation_used_for_uncertainty_calibration": False,
        "validation_status": "PASS" if overall == "PASS" else "FAIL",
        "uncertainty_calibration_status": (
            "PASS"
            if overall == "PASS" and validation.get("gates", {}).get("scaled_interval_coverage") is True
            else "FAIL"
        ),
        "uncertainty_calibration": calibration,
        "validation": validation,
        "candidate_priority_only": True,
        "predictions_are_final_labels": False,
        "anchor_frequencies_ghz": list(ANCHOR_FREQUENCIES_GHZ),
        "predicted_features": list(PREDICTED_FEATURES),
        "member_count": len(members),
        "members": members,
        "hyperparameters": {
            "model_family": "independently_seeded_random_feature_ridge_forward_ensemble",
            "hidden_features": int(args.hidden_features),
            "ridge": float(args.ridge),
            "bootstrap_fraction": float(args.bootstrap_fraction),
            "train_fraction": float(args.train_fraction),
            "calibration_fraction": float(args.calibration_fraction),
            "interval_target": float(args.interval_target),
        },
        "checks": checks,
        "scientific_boundary": (
            "PASS authorizes candidate ranking only. Model means and uncertainty are not EMX labels, accepted rows, "
            "coverage counts, execution completion, or physical validation."
        ),
    }
    receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _write_sha256s(out_dir)
    print(f"overall_status={overall}")
    print(f"decision={receipt['decision']}")
    print(f"training_geometry_count={training_count}")
    print(f"calibration_geometry_count={calibration_count}")
    print(f"validation_geometry_count={validation_count}")
    print(f"ensemble_receipt={receipt_path}")
    return 0 if overall == "PASS" else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--checkpoint-audit-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--member-seed", type=int, action="append")
    parser.add_argument("--split-seed", type=int, default=20260828)
    parser.add_argument("--hidden-features", type=int, default=DEFAULT_HIDDEN_FEATURES)
    parser.add_argument("--ridge", type=float, default=DEFAULT_RIDGE)
    parser.add_argument("--bootstrap-fraction", type=float, default=1.0)
    parser.add_argument("--train-fraction", type=float, default=DEFAULT_TRAIN_FRACTION)
    parser.add_argument("--calibration-fraction", type=float, default=DEFAULT_CALIBRATION_FRACTION)
    parser.add_argument("--interval-target", type=float, default=DEFAULT_INTERVAL_TARGET)
    return parser.parse_args(argv)


def _load_checkpoint(audit_dir: Path, fingerprint: str) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    status_path = audit_dir / "CHECKPOINT_STATUS.json"
    receipt_path = audit_dir / "CHECKPOINT_RECEIPT.json"
    status = _read_json(status_path, checks, "checkpoint_status")
    receipt = _read_json(receipt_path, checks, "checkpoint_receipt")
    accepted = _integer(status.get("accepted_geometries"))
    receipt_checks = receipt.get("checks") if isinstance(receipt.get("checks"), list) else []
    checks.extend(
        [
            _check(
                "checkpoint_status_is_real_emx_complete",
                status.get("campaign_id") == CAMPAIGN_ID
                and status.get("contract_fingerprint_sha256") == fingerprint
                and status.get("checkpoint_status") in {"CHECKPOINT_COMPLETE", f"ROUND_{accepted}_COMPLETE"}
                and _integer(status.get("s4p_artifacts")) == accepted
                and _integer(status.get("geometry_frequency_rows")) == accepted * FREQUENCY_POINTS,
                status,
            ),
            _check(
                "checkpoint_receipt_pass",
                receipt.get("overall_status") == "PASS"
                and receipt.get("decision") == "USE_CHECKPOINT"
                and receipt.get("campaign_id") == CAMPAIGN_ID
                and receipt.get("contract_fingerprint_sha256") == fingerprint
                and _integer(receipt.get("expected_accepted")) == accepted
                and bool(receipt_checks)
                and all(isinstance(item, dict) and item.get("pass") is True for item in receipt_checks),
                receipt.get("decision"),
            ),
        ]
    )
    outputs = receipt.get("outputs") if isinstance(receipt.get("outputs"), dict) else {}
    inputs = receipt.get("inputs") if isinstance(receipt.get("inputs"), dict) else {}
    status_evidence = outputs.get("checkpoint_status") if isinstance(outputs.get("checkpoint_status"), dict) else {}
    accepted_evidence = inputs.get("accepted_geometries") if isinstance(inputs.get("accepted_geometries"), dict) else {}
    features_evidence = inputs.get("long_features") if isinstance(inputs.get("long_features"), dict) else {}
    bounds_evidence = inputs.get("geometry_bounds") if isinstance(inputs.get("geometry_bounds"), dict) else {}
    accepted_path = Path(str(accepted_evidence.get("path") or "")).expanduser().resolve()
    features_path = Path(str(features_evidence.get("path") or "")).expanduser().resolve()
    bounds_path = Path(str(bounds_evidence.get("path") or "")).expanduser().resolve()
    checks.extend(
        [
            _check("checkpoint_status_hash_bound", _evidence_matches(status_path, status_evidence), str(status_path)),
            _check("accepted_geometries_hash_bound", _evidence_matches(accepted_path, accepted_evidence), str(accepted_path)),
            _check("long_features_hash_bound", _evidence_matches(features_path, features_evidence), str(features_path)),
            _check("geometry_bounds_hash_bound", _evidence_matches(bounds_path, bounds_evidence), str(bounds_path)),
        ]
    )
    return {
        "checks": checks,
        "accepted_count": accepted,
        "checkpoint_receipt_path": str(receipt_path),
        "checkpoint_receipt_sha256": _sha256(receipt_path) if receipt_path.is_file() else None,
        "accepted_geometries_path": str(accepted_path),
        "long_features_path": str(features_path),
        "geometry_bounds_path": str(bounds_path),
    }


def _load_accepted(
    path: Path,
    *,
    fingerprint: str,
    expected_count: int,
    bounds: dict[str, Any],
) -> dict[str, Any]:
    rows, fields = _read_csv(path)
    required = {
        "geometry_id",
        "geometry_sha256",
        "campaign_contract_fingerprint",
        *(f"geom__{name}" for name in GEOMETRY_FIELDS),
    }
    errors: list[str] = []
    geometry_ids: list[str] = []
    geometry_hashes: list[str] = []
    matrix: list[list[float]] = []
    phases: list[str] = []
    for row_index, row in enumerate(rows, start=2):
        geometry_id = str(row.get("geometry_id") or "").strip()
        digest = str(row.get("geometry_sha256") or "").strip().lower()
        try:
            values = [float(row[f"geom__{name}"]) for name in GEOMETRY_FIELDS]
        except (KeyError, TypeError, ValueError):
            errors.append(f"line {row_index}: invalid geometry values")
            continue
        geometry = {name: value for name, value in zip(GEOMETRY_FIELDS, values)}
        if not geometry_id or digest != canonical_geometry_sha256(geometry):
            errors.append(f"line {row_index}: geometry identity mismatch")
        if row.get("campaign_contract_fingerprint") != fingerprint:
            errors.append(f"line {row_index}: campaign fingerprint mismatch")
        geometry_ids.append(geometry_id)
        geometry_hashes.append(digest)
        matrix.append(values)
        phases.append(str(row.get("campaign_phase") or ""))
    normalized, normalization_errors = _normalize_geometry(matrix, bounds)
    errors.extend(normalization_errors)
    checks = [
        _check("accepted_columns_present", required.issubset(fields), sorted(required - fields)),
        _check("accepted_count_exact", len(rows) == expected_count, {"actual": len(rows), "expected": expected_count}),
        _check("accepted_rows_valid", not errors, errors[:20]),
        _check("accepted_geometry_ids_unique", len(set(geometry_ids)) == len(rows), len(set(geometry_ids))),
        _check("accepted_geometry_hashes_unique", len(set(geometry_hashes)) == len(rows), len(set(geometry_hashes))),
    ]
    return {
        "checks": checks,
        "rows": rows,
        "geometry_ids": geometry_ids,
        "geometry_hashes": geometry_hashes,
        "normalized": normalized,
        "phases": phases,
    }


def _load_anchor_features(
    path: Path,
    *,
    geometry_ids: list[str],
    geometry_hashes: list[str],
    fingerprint: str,
) -> dict[str, Any]:
    id_to_index = {value: index for index, value in enumerate(geometry_ids)}
    expected_hashes = dict(zip(geometry_ids, geometry_hashes))
    continuous = np.full(
        (len(geometry_ids), len(ANCHOR_FREQUENCIES_GHZ), len(CONTINUOUS_FEATURES)),
        np.nan,
        dtype=float,
    )
    validity = np.zeros((len(geometry_ids), len(ANCHOR_FREQUENCIES_GHZ)), dtype=bool)
    seen = np.zeros_like(validity, dtype=bool)
    required = {
        "geometry_id",
        "geometry_sha256",
        "campaign_contract_fingerprint",
        "frequency_hz",
        *CONTINUOUS_FEATURES,
        "broadband_descriptor_valid",
    }
    errors: list[str] = []
    fields: set[str] = set()
    anchors_hz = {int(anchor * 1_000_000_000): index for index, anchor in enumerate(ANCHOR_FREQUENCIES_GHZ)}
    if path.is_file():
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or ())
            for row_index, row in enumerate(reader, start=2):
                frequency_hz = _integer(row.get("frequency_hz"))
                anchor_index = anchors_hz.get(frequency_hz)
                if anchor_index is None:
                    continue
                geometry_id = str(row.get("geometry_id") or "").strip()
                geometry_index = id_to_index.get(geometry_id)
                if geometry_index is None:
                    errors.append(f"line {row_index}: anchor row geometry is not accepted")
                    continue
                if seen[geometry_index, anchor_index]:
                    errors.append(f"line {row_index}: duplicate geometry/anchor feature row")
                    continue
                seen[geometry_index, anchor_index] = True
                if str(row.get("geometry_sha256") or "").lower() != expected_hashes[geometry_id]:
                    errors.append(f"line {row_index}: geometry hash mismatch")
                if row.get("campaign_contract_fingerprint") != fingerprint:
                    errors.append(f"line {row_index}: campaign fingerprint mismatch")
                is_valid = _boolean(row.get("broadband_descriptor_valid"))
                if is_valid is None:
                    errors.append(f"line {row_index}: invalid broadband descriptor flag")
                    continue
                validity[geometry_index, anchor_index] = is_valid
                if is_valid:
                    values = [_finite(row.get(feature)) for feature in CONTINUOUS_FEATURES]
                    if any(value is None for value in values):
                        errors.append(f"line {row_index}: valid descriptor has non-finite feature")
                        continue
                    continuous[geometry_index, anchor_index, :] = np.asarray(values, dtype=float)
                    qp = float(row["qp"])
                    qs = float(row["qs"])
                    qmin = float(row["qmin"])
                    if not math.isclose(qmin, min(qp, qs), rel_tol=0.0, abs_tol=1.0e-6):
                        errors.append(f"line {row_index}: qmin differs from min(qp,qs)")
    checks = [
        _check("long_features_exists", path.is_file(), str(path)),
        _check("long_features_columns_present", required.issubset(fields), sorted(required - fields)),
        _check("anchor_feature_rows_exact", bool(np.all(seen)), {"actual": int(np.sum(seen)), "expected": int(seen.size)}),
        _check("anchor_feature_rows_valid", not errors, errors[:20]),
        _check("every_anchor_has_valid_training_support", all(np.any(validity[:, index]) for index in range(validity.shape[1])), validity.sum(axis=0).astype(int).tolist()),
    ]
    return {"checks": checks, "continuous": continuous, "validity": validity}


def _write_split_manifest(path: Path, accepted: dict[str, Any], split: Any) -> None:
    labels = np.full(len(accepted["geometry_ids"]), "", dtype=object)
    labels[split.train_indices] = "train"
    labels[split.calibration_indices] = "calibration"
    labels[split.validation_indices] = "sealed_validation"
    rows = [
        {
            "geometry_id": accepted["geometry_ids"][index],
            "geometry_sha256": accepted["geometry_hashes"][index],
            "campaign_phase": accepted["phases"][index],
            "ensemble_split": str(labels[index]),
        }
        for index in range(len(labels))
    ]
    _write_csv(path, rows)


def _normalize_geometry(matrix: list[list[float]], bounds: dict[str, Any]) -> tuple[np.ndarray, list[str]]:
    array = np.asarray(matrix, dtype=float)
    errors: list[str] = []
    if array.shape != (len(matrix), len(GEOMETRY_FIELDS)) or len(matrix) < 1:
        return np.empty((0, len(GEOMETRY_FIELDS))), ["geometry matrix shape is invalid"]
    try:
        lower = np.asarray([float(bounds[name][0]) for name in GEOMETRY_FIELDS], dtype=float)
        upper = np.asarray([float(bounds[name][1]) for name in GEOMETRY_FIELDS], dtype=float)
    except (KeyError, TypeError, ValueError, IndexError):
        return np.empty_like(array), ["geometry bounds are invalid"]
    normalized = (array - lower[None, :]) / (upper - lower)[None, :]
    outside = np.argwhere((normalized < -1.0e-12) | (normalized > 1.0 + 1.0e-12))
    if outside.size:
        errors.append(f"geometry values outside frozen bounds: {outside[:20].tolist()}")
    if not np.all(np.isfinite(normalized)):
        errors.append("geometry normalization is non-finite")
    return np.clip(normalized, 0.0, 1.0), errors


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
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
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


def _evidence_matches(path: Path, evidence: dict[str, Any]) -> bool:
    return (
        path.is_file()
        and str(evidence.get("path") or "") == str(path)
        and str(evidence.get("sha256") or "").lower() == _sha256(path)
    )


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


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _boolean(value: Any) -> bool | None:
    text = str(value).strip().lower()
    if text in {"1", "true", "pass", "yes"}:
        return True
    if text in {"0", "false", "fail", "no"}:
        return False
    return None


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": str(name), "pass": bool(passed), "detail": detail}


if __name__ == "__main__":
    raise SystemExit(main())
