#!/usr/bin/env python3
"""Build one contract-bound Phase-B/C geometry-only candidate pool."""

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
from scipy.stats import qmc

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rfic_transformer_inverse_design.api import (  # noqa: E402
    TransformerOptimizationAdapter,
    load_run_config,
)
from rfic_transformer_inverse_design.campaigns.broadband56_adaptive_selection import (  # noqa: E402
    MINIMUM_CANDIDATE_POOL_FACTOR,
    required_prediction_columns,
)
from rfic_transformer_inverse_design.campaigns.broadband56_balanced200k import (  # noqa: E402
    ADAPTIVE_BATCH_SIZE,
    CAMPAIGN_ID,
    FREQUENCY_GRID_HZ,
    GEOMETRY_FIELDS,
    adaptive_round_spec,
    canonical_geometry_bounds,
    canonical_geometry_sha256,
    contract_fingerprint,
    validate_contract,
)
from rfic_transformer_inverse_design.campaigns.broadband56_geometry_coverage import (  # noqa: E402
    validate_geometry_bounds_payload,
)
from rfic_transformer_inverse_design.layout.drc_rules import (  # noqa: E402
    audit_tsmc65_top_metal_geometry,
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


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    contract_path = Path(args.contract).expanduser().resolve()
    config_path = Path(args.config).expanduser().resolve()
    round_dir = Path(args.round_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    if out_dir.exists():
        print(f"refusing to overwrite existing output directory: {out_dir}", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True)

    checks: list[dict[str, Any]] = []
    contract = _read_json(contract_path, checks, "campaign_contract")
    contract_errors = validate_contract(contract) if contract else ["contract unavailable"]
    checks.append(_check("campaign_contract_valid", not contract_errors, contract_errors))
    fingerprint = (
        str(contract.get("contract_fingerprint_sha256") or contract_fingerprint(contract))
        if contract
        else ""
    )

    round_contract_path = round_dir / "ADAPTIVE_ROUND_CONTRACT.json"
    round_receipt_path = round_dir / "ADAPTIVE_ROUND_RECEIPT.json"
    round_contract = _read_json(round_contract_path, checks, "adaptive_round_contract")
    round_receipt = _read_json(round_receipt_path, checks, "adaptive_round_receipt")
    round_info = round_contract.get("round") if isinstance(round_contract.get("round"), dict) else {}
    accepted_start = _integer(round_info.get("accepted_start"))
    try:
        expected_round = adaptive_round_spec(accepted_start)
    except ValueError as exc:
        expected_round = None
        checks.append(_check("adaptive_round_boundary_valid", False, str(exc)))
    else:
        checks.append(
            _check(
                "adaptive_round_contract_exact",
                round_info == expected_round.as_dict(),
                {"actual": round_info, "expected": expected_round.as_dict()},
            )
        )
    checks.extend(
        [
            _check(
                "adaptive_round_contract_pass",
                round_contract.get("overall_status") == "PASS"
                and round_contract.get("campaign_id") == CAMPAIGN_ID
                and round_contract.get("campaign_contract_fingerprint") == fingerprint
                and round_contract.get("acquisition_mode")
                in {"ENSEMBLE_ACQUISITION", "FALLBACK_MAXIMIN"},
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
                _output_evidence_matches(
                    round_receipt,
                    "adaptive_round_contract",
                    round_contract_path,
                ),
                str(round_contract_path),
            ),
        ]
    )

    preceding = (
        round_contract.get("preceding_real_emx_audit")
        if isinstance(round_contract.get("preceding_real_emx_audit"), dict)
        else {}
    )
    accepted_path = Path(str(preceding.get("accepted_geometries_path") or "")).expanduser().resolve()
    bounds_path = Path(str(preceding.get("geometry_bounds_path") or "")).expanduser().resolve()
    checks.extend(
        [
            _check(
                "accepted_ledger_hash_bound",
                _path_sha_matches(
                    accepted_path,
                    str(preceding.get("accepted_geometries_sha256") or ""),
                ),
                str(accepted_path),
            ),
            _check(
                "geometry_bounds_hash_bound",
                _path_sha_matches(
                    bounds_path,
                    str(preceding.get("geometry_bounds_sha256") or ""),
                ),
                str(bounds_path),
            ),
        ]
    )
    bounds_payload = _read_json(bounds_path, checks, "geometry_bounds")
    bounds_errors = (
        validate_geometry_bounds_payload(
            bounds_payload,
            contract_fingerprint_sha256=fingerprint,
        )
        if bounds_payload
        else ["geometry bounds unavailable"]
    )
    checks.append(_check("geometry_bounds_contract_valid", not bounds_errors, bounds_errors))
    bounds = (
        bounds_payload.get("field_bounds_um")
        if isinstance(bounds_payload.get("field_bounds_um"), dict)
        else {}
    )

    config = _load_config(config_path, checks)
    adapter = None
    if config is not None:
        _validate_config(config, checks)
        adapter = TransformerOptimizationAdapter(config.bounds)
        config_bounds = canonical_geometry_bounds(adapter)
        checks.append(
            _check(
                "config_bounds_match_frozen_bounds",
                _bounds_equal(config_bounds, bounds),
                {
                    "config": {name: list(config_bounds.get(name, ())) for name in GEOMETRY_FIELDS},
                    "frozen": bounds,
                },
            )
        )

    accepted = _load_accepted(
        accepted_path,
        fingerprint=fingerprint,
        expected_count=accepted_start,
        bounds=bounds,
    )
    checks.extend(accepted.pop("checks"))
    extra_excluded = _read_excluded_hashes(
        [Path(value).expanduser().resolve() for value in args.exclude_geometry_csv],
        checks,
    )
    requested_count = int(args.count)
    checks.extend(
        [
            _check("requested_count_positive", requested_count > 0, requested_count),
            _check(
                "requested_count_meets_pool_minimum",
                requested_count >= MINIMUM_CANDIDATE_POOL_FACTOR * ADAPTIVE_BATCH_SIZE,
                {
                    "actual": requested_count,
                    "minimum": MINIMUM_CANDIDATE_POOL_FACTOR * ADAPTIVE_BATCH_SIZE,
                },
            ),
            _check(
                "oversample_factor_valid",
                math.isfinite(float(args.oversample_factor))
                and float(args.oversample_factor) >= 1.0,
                args.oversample_factor,
            ),
            _check(
                "max_sampling_rounds_positive",
                int(args.max_sampling_rounds) >= 1,
                args.max_sampling_rounds,
            ),
            _check("candidate_seed_nonnegative", int(args.seed) >= 0, args.seed),
        ]
    )

    rows: list[dict[str, Any]] = []
    attempts = 0
    analytical_rejections = 0
    accepted_duplicate_rejections = 0
    extra_exclusion_rejections = 0
    intra_pool_duplicate_rejections = 0
    rejection_examples: list[str] = []
    if adapter is not None and expected_round is not None and all(item["pass"] for item in checks):
        accepted_hashes = set(accepted["hashes"])
        seen = set(accepted_hashes) | set(extra_excluded)
        lower = np.asarray([float(bounds[name][0]) for name in GEOMETRY_FIELDS], dtype=float)
        upper = np.asarray([float(bounds[name][1]) for name in GEOMETRY_FIELDS], dtype=float)
        for sampling_round in range(int(args.max_sampling_rounds)):
            remaining = requested_count - len(rows)
            if remaining <= 0:
                break
            sample_count = max(
                remaining,
                int(math.ceil(remaining * float(args.oversample_factor))),
            )
            sampling_seed = int(args.seed) + sampling_round
            unit = _sample_unit(
                sample_count,
                len(GEOMETRY_FIELDS),
                str(args.sampler),
                sampling_seed,
            )
            values = qmc.scale(unit, lower, upper)
            for vector, unit_vector in zip(values, unit):
                attempts += 1
                geometry_values = {
                    name: float(value) for name, value in zip(GEOMETRY_FIELDS, vector)
                }
                try:
                    geometry = _geometry_from_campaign_values(adapter, geometry_values)
                except Exception as exc:  # noqa: BLE001
                    analytical_rejections += 1
                    if len(rejection_examples) < 20:
                        rejection_examples.append(
                            f"attempt {attempts}: geometry build: {type(exc).__name__}: {exc}"
                        )
                    continue
                errors = [*config.bounds.validate(geometry), *geometry.validate()]
                drc = audit_tsmc65_top_metal_geometry(geometry, config)
                if not bool(drc.get("ok")):
                    errors.extend(f"DRC: {value}" for value in drc.get("errors") or [])
                if errors:
                    analytical_rejections += 1
                    if len(rejection_examples) < 20:
                        rejection_examples.append(
                            f"attempt {attempts}: {'; '.join(errors)}"
                        )
                    continue
                flat = geometry.flat_dict()
                geometry_hash = canonical_geometry_sha256(flat)
                if geometry_hash in accepted_hashes:
                    accepted_duplicate_rejections += 1
                    continue
                if geometry_hash in extra_excluded:
                    extra_exclusion_rejections += 1
                    continue
                if geometry_hash in seen:
                    intra_pool_duplicate_rejections += 1
                    continue
                seen.add(geometry_hash)
                rows.append(
                    _candidate_row(
                        index=len(rows),
                        flat=flat,
                        unit_vector=unit_vector,
                        geometry_hash=geometry_hash,
                        fingerprint=fingerprint,
                        expected_round=expected_round,
                        sampler=str(args.sampler),
                        sampling_seed=sampling_seed,
                        round_contract_sha256=_sha256(round_contract_path),
                        drc=drc,
                    )
                )
                if len(rows) >= requested_count:
                    break

    row_hashes = [str(row.get("geometry_sha256") or "") for row in rows]
    checks.extend(
        [
            _check(
                "candidate_pool_count_exact",
                len(rows) == requested_count,
                {"actual": len(rows), "expected": requested_count},
            ),
            _check(
                "candidate_geometry_hashes_unique",
                len(set(row_hashes)) == len(rows),
                len(set(row_hashes)),
            ),
            _check(
                "candidate_geometries_disjoint_from_accepted",
                not (set(row_hashes) & set(accepted.get("hashes") or ())),
                len(set(row_hashes) & set(accepted.get("hashes") or ())),
            ),
            _check(
                "candidate_rows_are_geometry_only",
                all(
                    not _looks_like_response_label(column)
                    for row in rows
                    for column in row
                ),
                "no response, proxy, S/Z, or EMX label columns",
            ),
            _check(
                "candidate_rows_have_no_prediction_columns",
                all(column not in row for row in rows for column in required_prediction_columns()),
                "predictions are attached only by the separate ensemble predictor",
            ),
            _check(
                "candidate_local_gates_pass",
                all(
                    row.get("analytical_status") == "PASS"
                    and row.get("topology_status") == "PASS"
                    and row.get("top_metal_drc_status") == "PASS"
                    for row in rows
                ),
                len(rows),
            ),
        ]
    )
    overall = "PASS" if checks and all(item["pass"] for item in checks) else "FAIL"
    pool_path = out_dir / "broadband56_adaptive_candidate_pool.csv"
    if overall == "PASS":
        _write_csv(pool_path, rows)

    summary_path = out_dir / "ADAPTIVE_CANDIDATE_POOL_SUMMARY.json"
    summary = {
        "schema": "broadband56_adaptive_candidate_pool_summary_v1",
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall,
        "decision": (
            "USE_AS_UNEVALUATED_GEOMETRY_CANDIDATE_POOL"
            if overall == "PASS"
            else "DO_NOT_USE_OR_RUN_CADENCE_CALIBRE_OR_EMX"
        ),
        "campaign_id": CAMPAIGN_ID,
        "campaign_contract_fingerprint": fingerprint,
        "round": expected_round.as_dict() if expected_round is not None else round_info,
        "acquisition_mode": round_contract.get("acquisition_mode"),
        "sampler": str(args.sampler),
        "seed": int(args.seed),
        "requested_count": requested_count,
        "candidate_count": len(rows),
        "sampling_attempts": attempts,
        "analytical_or_top_metal_rejections": analytical_rejections,
        "accepted_duplicate_rejections": accepted_duplicate_rejections,
        "extra_exclusion_rejections": extra_exclusion_rejections,
        "intra_pool_duplicate_rejections": intra_pool_duplicate_rejections,
        "accepted_geometry_count": len(accepted.get("hashes") or ()),
        "extra_excluded_geometry_count": len(extra_excluded),
        "candidate_geometry_sha256_digest": _string_digest(row_hashes) if row_hashes else None,
        "inputs": {
            "campaign_contract": _file_evidence(contract_path),
            "production_config": _file_evidence(config_path),
            "adaptive_round_contract": _file_evidence(round_contract_path),
            "adaptive_round_receipt": _file_evidence(round_receipt_path),
            "accepted_geometries": _file_evidence(accepted_path),
            "geometry_bounds": _file_evidence(bounds_path),
        },
        "output": _file_evidence(pool_path) if pool_path.is_file() else None,
        "checks": checks,
        "rejection_examples": rejection_examples,
        "scientific_boundary": (
            "The pool is geometry-only and unevaluated. Local analytical/top-metal checks are not Cadence, "
            "Calibre, GDS, EMX, accepted-data, response-label, or physical-coverage evidence."
        ),
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    receipt_path = out_dir / "ADAPTIVE_CANDIDATE_POOL_RECEIPT.json"
    receipt = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": overall,
        "decision": summary["decision"],
        "campaign_id": CAMPAIGN_ID,
        "campaign_contract_fingerprint": fingerprint,
        "checks": checks,
        "outputs": {
            "candidate_pool_summary": _file_evidence(summary_path),
            "candidate_pool": _file_evidence(pool_path) if pool_path.is_file() else None,
        },
    }
    receipt_path.write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _write_sha256s(out_dir)
    print(f"overall_status={overall}")
    print(f"decision={summary['decision']}")
    print(f"candidate_count={len(rows)}")
    if pool_path.is_file():
        print(f"candidate_pool={pool_path}")
    print(f"receipt={receipt_path}")
    return 0 if overall == "PASS" else 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--round-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--sampler", choices=("lhs_optimized", "sobol"), default="sobol")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--exclude-geometry-csv", action="append", default=[])
    parser.add_argument("--oversample-factor", type=float, default=1.25)
    parser.add_argument("--max-sampling-rounds", type=int, default=20)
    return parser.parse_args(argv)


def _load_accepted(
    path: Path,
    *,
    fingerprint: str,
    expected_count: int,
    bounds: dict[str, Any],
) -> dict[str, Any]:
    rows, fields = _read_csv(path)
    required = {
        "geometry_sha256",
        "campaign_contract_fingerprint",
        "calibre_blocking_violations",
        *ACCEPTANCE_STATUS_FIELDS,
        *(f"geom__{name}" for name in GEOMETRY_FIELDS),
    }
    errors: list[str] = []
    hashes: list[str] = []
    for row_index, row in enumerate(rows, start=2):
        try:
            geometry = {name: float(row[f"geom__{name}"]) for name in GEOMETRY_FIELDS}
        except (KeyError, TypeError, ValueError):
            errors.append(f"line {row_index}: invalid geometry values")
            continue
        digest = str(row.get("geometry_sha256") or "").strip().lower()
        if digest != canonical_geometry_sha256(geometry):
            errors.append(f"line {row_index}: canonical geometry hash mismatch")
        if row.get("campaign_contract_fingerprint") != fingerprint:
            errors.append(f"line {row_index}: campaign fingerprint mismatch")
        if any(str(row.get(field) or "").upper() != "PASS" for field in ACCEPTANCE_STATUS_FIELDS):
            errors.append(f"line {row_index}: accepted gate is not PASS")
        if _integer(row.get("calibre_blocking_violations")) != 0:
            errors.append(f"line {row_index}: blocking Calibre violations are nonzero")
        for name, value in geometry.items():
            try:
                low, high = float(bounds[name][0]), float(bounds[name][1])
            except (KeyError, TypeError, ValueError, IndexError):
                errors.append(f"line {row_index}: frozen bound unavailable for {name}")
                continue
            if not low <= value <= high:
                errors.append(f"line {row_index}: {name} outside frozen bounds")
        hashes.append(digest)
    checks = [
        _check("accepted_columns_present", required.issubset(fields), sorted(required - fields)),
        _check(
            "accepted_count_matches_round_start",
            len(rows) == expected_count,
            {"actual": len(rows), "expected": expected_count},
        ),
        _check("accepted_rows_valid", not errors, errors[:20]),
        _check(
            "accepted_geometry_hashes_unique",
            len(set(hashes)) == len(rows),
            {"unique": len(set(hashes)), "rows": len(rows)},
        ),
    ]
    return {"checks": checks, "hashes": hashes}


def _read_excluded_hashes(
    paths: list[Path],
    checks: list[dict[str, Any]],
) -> set[str]:
    excluded: set[str] = set()
    for path in paths:
        rows, fields = _read_csv(path)
        checks.append(_check("excluded_geometry_csv_exists", path.is_file(), str(path)))
        checks.append(
            _check(
                "excluded_geometry_csv_has_identity",
                "geometry_sha256" in fields or "geometry_id" in fields,
                str(path),
            )
        )
        for row in rows:
            value = str(
                row.get("geometry_sha256") or row.get("geometry_id") or ""
            ).strip().lower()
            if value:
                excluded.add(value)
    return excluded


def _candidate_row(
    *,
    index: int,
    flat: dict[str, Any],
    unit_vector: np.ndarray,
    geometry_hash: str,
    fingerprint: str,
    expected_round: Any,
    sampler: str,
    sampling_seed: int,
    round_contract_sha256: str,
    drc: dict[str, Any],
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "candidate_pool_index": index,
        "candidate_id": f"b56v2_pool_{geometry_hash[:16]}",
        "campaign_id": CAMPAIGN_ID,
        "campaign_contract_fingerprint": fingerprint,
        "campaign_phase": expected_round.phase,
        "acquisition_source": "unevaluated_adaptive_candidate_pool",
        "round_id": expected_round.round_id,
        "round_accepted_start": expected_round.accepted_start,
        "round_accepted_target": expected_round.accepted_target,
        "adaptive_round_contract_sha256": round_contract_sha256,
        "geometry_id": geometry_hash,
        "geometry_sha256": geometry_hash,
        "geometry_fingerprint_sha256": geometry_hash,
        "geometry_fingerprint_schema": "ordered_10d_um_sha256_v2",
        "geometry_fingerprint_quantization_um": "1e-9",
        "analytical_status": "PASS",
        "topology_status": "PASS",
        "top_metal_drc_status": "PASS",
        "drc_status": str(drc.get("status") or "PASS"),
        "drc_rule_source": str(drc.get("rule_source") or ""),
        "candidate_generation_mode": f"{sampler}_normalized_10d_adaptive_pool",
        "candidate_generation_seed": int(sampling_seed),
        "label_status": "UNEVALUATED_GEOMETRY_ONLY",
        "predictions_are_labels": "false",
    }
    for name, unit_value in zip(GEOMETRY_FIELDS, unit_vector):
        row[f"geom__{name}"] = float(flat[name])
        row[f"unit__{name}"] = float(unit_value)
    return row


def _sample_unit(count: int, dimensions: int, sampler: str, seed: int) -> np.ndarray:
    if sampler == "lhs_optimized":
        return qmc.LatinHypercube(
            d=dimensions,
            seed=seed,
            optimization="random-cd",
        ).random(n=count)
    engine = qmc.Sobol(d=dimensions, scramble=True, seed=seed)
    exponent = int(math.ceil(math.log2(max(1, count))))
    return engine.random_base2(m=exponent)[:count]


def _geometry_from_campaign_values(
    adapter: TransformerOptimizationAdapter,
    values: dict[str, float],
) -> Any:
    vector = []
    for name in adapter.field_order():
        if name in {"primary_width_um", "secondary_width_um"}:
            vector.append(values["line_width_um"])
        else:
            vector.append(values[name])
    return adapter.from_vector(vector).with_shared_line_width(values["line_width_um"])


def _validate_config(config: Any, checks: list[dict[str, Any]]) -> None:
    grid = tuple(int(round(value)) for value in config.target.frequency_points_hz())
    power = config.emx.power_line_8port
    checks.extend(
        [
            _check("frequency_grid_exact_56", grid == FREQUENCY_GRID_HZ, str(grid)),
            _check(
                "port_mode_grounded",
                str(config.emx.port_mode) == "single_ended_shield_grounded",
                config.emx.port_mode,
            ),
            _check(
                "touchstone_mode_s4p",
                str(power.touchstone_mode) == "signal_4_grounded_aux",
                power.touchstone_mode,
            ),
            _check(
                "signal_port_map_exact",
                tuple(power.port_map) == ("P001", "P002", "P003", "P004"),
                power.port_map,
            ),
            _check(
                "cadence_pin_purpose_51",
                int(config.emx.cadence_pin_purpose) == 51,
                config.emx.cadence_pin_purpose,
            ),
        ]
    )


def _bounds_equal(
    actual: dict[str, tuple[float, float]],
    expected: dict[str, Any],
) -> bool:
    if set(actual) != set(GEOMETRY_FIELDS) or set(expected) != set(GEOMETRY_FIELDS):
        return False
    try:
        return all(
            math.isclose(float(actual[name][0]), float(expected[name][0]), rel_tol=0.0, abs_tol=1.0e-12)
            and math.isclose(float(actual[name][1]), float(expected[name][1]), rel_tol=0.0, abs_tol=1.0e-12)
            for name in GEOMETRY_FIELDS
        )
    except (KeyError, TypeError, ValueError, IndexError):
        return False


def _looks_like_response_label(name: str) -> bool:
    lowered = name.lower()
    blocked = (
        "lp_nh",
        "ls_nh",
        "qp",
        "qs",
        "qmin",
        "signed_k",
        "k_abs",
        "xp_ohm",
        "xs_ohm",
        "s11_",
        "z11_",
        "pred__",
        "unc__",
    )
    return not lowered.startswith(("geom__", "unit__")) and any(
        token in lowered for token in blocked
    )


def _load_config(path: Path, checks: list[dict[str, Any]]) -> Any | None:
    if not path.is_file():
        checks.append(_check("config_exists", False, str(path)))
        return None
    checks.append(_check("config_exists", True, str(path)))
    try:
        config = load_run_config(path)
    except Exception as exc:  # noqa: BLE001
        checks.append(_check("config_loads", False, f"{type(exc).__name__}: {exc}"))
        return None
    checks.append(_check("config_loads", True, str(path)))
    return config


def _read_csv(path: Path) -> tuple[list[dict[str, str]], set[str]]:
    if not path.is_file():
        return [], set()
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return list(reader), set(reader.fieldnames or ())


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("refusing to write an empty candidate pool")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _read_json(
    path: Path,
    checks: list[dict[str, Any]],
    name: str,
) -> dict[str, Any]:
    if not path.is_file():
        checks.append(_check(f"{name}_exists", False, str(path)))
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        checks.append(_check(f"{name}_parses", False, f"{type(exc).__name__}: {exc}"))
        return {}
    checks.append(_check(f"{name}_parses", isinstance(payload, dict), type(payload).__name__))
    return payload if isinstance(payload, dict) else {}


def _output_evidence_matches(
    receipt: dict[str, Any],
    name: str,
    path: Path,
) -> bool:
    outputs = receipt.get("outputs") if isinstance(receipt.get("outputs"), dict) else {}
    evidence = outputs.get(name) if isinstance(outputs.get(name), dict) else {}
    return (
        str(evidence.get("path") or "") == str(path)
        and _path_sha_matches(path, str(evidence.get("sha256") or ""))
    )


def _path_sha_matches(path: Path, expected_sha: str) -> bool:
    return path.is_file() and _is_sha256(expected_sha) and _sha256(path) == expected_sha.lower()


def _file_evidence(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.is_file(),
        "size_bytes": path.stat().st_size if path.is_file() else None,
        "sha256": _sha256(path) if path.is_file() else None,
    }


def _write_sha256s(directory: Path) -> None:
    files = sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.name != "SHA256SUMS.txt"
    )
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


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdef" for character in value.lower()
    )


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"name": str(name), "pass": bool(passed), "detail": detail}


if __name__ == "__main__":
    raise SystemExit(main())
